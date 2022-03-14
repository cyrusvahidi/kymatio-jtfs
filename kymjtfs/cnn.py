import os, pandas as pd, numpy as np, torch

from typing import Optional, Union

from torch.nn import functional as F
from torch import nn
import torchvision.models as models
from torchmetrics import Accuracy, ClasswiseWrapper

from torch.utils.data import Dataset, DataLoader
from pytorch_lightning.core.lightning import LightningModule
import pytorch_lightning as pl
import mirdata.datasets.medley_solos_db as msdb

from kymatio.torch import TimeFrequencyScatteringTorch1D as TimeFrequencyScattering1D

from kymjtfs.batch_norm import ScatteringBatchNorm

from joblib import Memory


class MedleySolosClassifier(LightningModule):
    def __init__(self, 
                 c = 1e-3,
                 in_shape = 2**16, 
                 J = 12, 
                 Q = 16, 
                 F = 4, 
                 T = 2**11, 
                 lr=5e-4, 
                 average='macro', 
                 jtfs_dir='/import/c4dm-datasets/medley-solos-db/jtfs/',
                 classes=['clarinet', 
                          'distorted electric guitar',
                          'female singer',
                          'flute',
                          'piano',
                          'tenor saxophone',
                          'trumpet',
                          'violin'],
                 csv='/import/c4dm-datasets/medley-solos-db/annotation/Medley-solos-DB_metadata.csv'):
        super().__init__()

        self.c = c
        self.in_shape = in_shape
        self.J = J
        self.Q = Q
        self.F = F
        self.T = T
        self.lr = lr
        self.acc_metric = Accuracy(num_classes=len(classes), average=average)
        self.classwise_acc = ClasswiseWrapper(Accuracy(num_classes=len(classes), average=None), 
                                                       labels=classes)
        

        self.jtfs_dir = jtfs_dir
        
        self.mu = torch.tensor(np.load(os.path.join(jtfs_dir, 'stats/mu.npy')))
        s1_channels = 4
        
        self.n_channels = len(self.mu) + (s1_channels - 1)
        
        self.s1_conv1 = nn.Sequential(
            # Unsqueeze(1),
            nn.Conv2d(1, s1_channels, kernel_size=(16, 1)),
            nn.ReLU(),
            nn.AvgPool2d(kernel_size=(4, 1), padding=(2, 0))
        )
        self.jtfs_bn = nn.BatchNorm2d(self.n_channels)
        
        self.setup_cnn(len(classes))

        self.loss = nn.CrossEntropyLoss(weight=self.get_class_weight(csv))
                                                 
        
    def setup_cnn(self, num_classes):
        self.conv_net = LeNet(num_classes, self.n_channels)
        # self.conv_net = models.efficientnet_b0()
        # # modify input channels 
        # self.conv_net.features[0][0] = nn.Conv2d(self.n_channels, 
        #                                          32, 
        #                                          kernel_size=(3, 3), stride=(2, 2), padding=(1, 1), 
        #                                          bias=False)
        # self.conv_net.classifier[1] = nn.Linear(in_features=1280, out_features=num_classes, bias=True)    

    def setup_jtfs(self):
        self.jtfs = TimeFrequencyScattering1D(
            shape=(self.in_shape, ),
            T=self.T,
            Q=self.Q,
            J=self.J,
            F=self.F,
            average_fr=True,
            max_pad_factor=1, 
            max_pad_factor_fr=1,
            out_3D=True,)
        
        n_channels = self._get_jtfs_out_dim()
        
        self.jtfs_dim = self._get_jtfs_out_dim()
        self.jtfs_channels = self.jtfs_dim[0]

    def get_class_weight(self, csv):
        df = pd.read_csv(csv)
        support = sorted(dict(df['instrument'].sort_values().value_counts()).items())
        total = sum(count for _, count in support)
        weight = [1  - (s / total) for _, s in support]
        return torch.tensor(weight)
        
    def forward(self, x):
        Sx = x
        s1, s2 = Sx[0].squeeze(1), Sx[1].squeeze(1)

        # apply mean normalization
        s1 = s1 / (self.c * self.mu[:1].type_as(s1))
        s2 = s2 / (self.c * self.mu[1:][None, :, None, None].type_as(s2))

        # s1 learnable frequential filter
        s1_conv = self.s1_conv1(s1.unsqueeze(1))
        s1_conv = F.pad(s1_conv, 
                   (0, 0, s2.shape[-2] - s1_conv.shape[-2], 0))
        
        sx = torch.cat([s1_conv, s2], dim=1)[:, :, :32, :]

        # log1p and batch norm
        sx = torch.log1p(sx)
        sx = self.jtfs_bn(sx)

        # conv net
        y = self.conv_net(sx)
        # y = F.log_softmax(y, dim=1)
        
        return y
    
        
    def step(self, batch, fold):
        Sx, y = batch
        logits = self(Sx)

        loss, acc = F.nll_loss(logits, y), self.acc_metric(logits, y)
        class_acc = self.classwise_acc(logits, y)
        class_acc = {k: float(v.detach()) for k, v in class_acc.items()}
        
        self.log(f'{fold}/loss', loss, on_step=True, on_epoch=True, prog_bar=True)
        self.log(f'{fold}/acc', acc, on_step=True, on_epoch=True, prog_bar=True)
        if fold == 'test':
            self.log(f'{fold}/classwise', class_acc, on_step=True, on_epoch=True, prog_bar=True)
        
        return {f'loss': loss, f'{fold}/acc': acc, f'{fold}/classwise': class_acc}
    
    def log_metrics(self, outputs, fold):
        keys = list(outputs[0].keys())
        for k in keys:
            metric = torch.stack([x[k] for x in outputs]).mean()
            self.log(f'{fold}/{k}', metric)
        
    def training_step(self, batch, batch_idx):
        return self.step(batch, fold='train')

    def validation_step(self, batch, batch_idx):
        return self.step(batch, fold='val')

    def test_step(self, batch, batch_idx):
        return self.step(batch, fold='test')
    
    def configure_optimizers(self):
        opt = torch.optim.Adam(self.parameters(), lr=self.lr, weight_decay=5e-4)
        scheduler = torch.optim.lr_scheduler.ExponentialLR(opt, gamma=0.95)
        return [opt], [scheduler]
    
    def _get_jtfs_out_dim(self):
        dummy_in = torch.randn(self.in_shape).cuda()
        sx = self.jtfs(dummy_in)
        s1 = self.s1_conv1(sx[0])
        s1 = F.pad(s1, (0, 0, sx[1].shape[-2] - s1.shape[-2], 0))
        S = torch.cat([s1, sx[1]], dim=1)[:, :, :32, :]
        out_dim = S.shape[1:3]
        return out_dim
    

class Unsqueeze(nn.Module):
    def __init__(self, dim=1):
        super(Unsqueeze, self).__init__()
        self.dim = dim

    def forward(self, x):
        return x.unsqueeze(self.dim)


class MedleySolosDB(Dataset):
    def __init__(self, 
                 jtfs=None,
                 data_dir='/import/c4dm-datasets/medley-solos-db/', 
                 subset='training',
                 feature_dir='jtfs'):
        super().__init__()
        
        self.msdb = msdb.Dataset(data_dir)
        self.audio_dir = os.path.join(data_dir, 'audio')
        self.csv_dir = os.path.join(data_dir, 'annotation')
        self.subset = subset
        if feature_dir:
            feature_dir = os.path.join(data_dir, feature_dir)
            self.feature_dir = os.path.join(feature_dir, subset)

        self.jtfs = jtfs

        df = pd.read_csv(os.path.join(self.csv_dir, 'Medley-solos-DB_metadata.csv'))
        self.df = df.loc[df['subset'] == subset]
        self.df.reset_index(inplace = True)

        # cachedir = '/import/c4dm-04/cv'
        # self.memory = Memory(cachedir, verbose=0)
        
    def build_fname(self, df_item, ext='.npy'):
        uuid = df_item['uuid4']
        instr_id = df_item['instrument_id']
        subset = df_item['subset']
        if self.feature_dir:
            s1 = f'Medley-solos-DB_{subset}-{instr_id}_{uuid}_S1{ext}'
            s2 = f'Medley-solos-DB_{subset}-{instr_id}_{uuid}_S2{ext}'
            return s1, s2
        else:
            return f'Medley-solos-DB_{subset}-{instr_id}_{uuid}{ext}'

    def __getitem__(self, idx):
        item = self.df.iloc[idx]

        fname = self.build_fname(item)
        y = int(item['instrument_id'])

        if self.feature_dir:
            s1_fname, s2_fname = fname 
            s1 = np.load(os.path.join(self.feature_dir, s1_fname))
            s2 = np.load(os.path.join(self.feature_dir, s2_fname))
            Sx = (s1, s2)
            return Sx, y
        else:
            audio, _ = msdb.load_audio(os.path.join(self.audio_dir, fname))
            x = audio
            return x, y, fname

    def __len__(self):
        return len(self.df)


def _load_jtfs(jtfs, audio):
    return jtfs(audio)


class MedleyDataModule(pl.LightningDataModule):
    def __init__(self, 
                 data_dir: str = '/import/c4dm-datasets/medley-solos-db/', 
                 batch_size: int = 32,
                 jtfs=None,):
        super().__init__()
        self.data_dir = data_dir
        self.batch_size = batch_size
        self.jtfs = jtfs

    def setup(self, stage: Optional[str] = None):
        self.train_ds = MedleySolosDB(self.jtfs, self.data_dir, subset='training')
        self.val_ds = MedleySolosDB(self.jtfs, self.data_dir, subset='validation')
        self.test_ds = MedleySolosDB(self.jtfs, self.data_dir, subset='test')

    def train_dataloader(self):
        return DataLoader(self.train_ds, batch_size=self.batch_size, shuffle=True, drop_last=True)

    def val_dataloader(self):
        return DataLoader(self.val_ds, batch_size=self.batch_size, shuffle=False, drop_last=True)

    def test_dataloader(self):
        return DataLoader(self.test_ds, batch_size=self.batch_size, shuffle=False, drop_last=True)

class LeNet(nn.Module):
    def __init__(self, num_classes, in_channels):
        super().__init__()
        self.feature_extractor = nn.Sequential(            
            nn.Conv2d(in_channels=in_channels, out_channels=6, kernel_size=5, stride=1),
            nn.ReLU(),
            nn.AvgPool2d(kernel_size=2),
            nn.Conv2d(in_channels=6, out_channels=16, kernel_size=5, stride=1),
            nn.ReLU(),
            nn.AvgPool2d(kernel_size=2),
            nn.Conv2d(in_channels=16, out_channels=120, kernel_size=5, stride=1),
            nn.ReLU()
        )

        self.classifier = nn.Sequential(
            nn.Linear(in_features=120, out_features=84),
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(in_features=84, out_features=num_classes),
        )

    def forward(self, x):
        return self.classifier(self.feature_extractor(x).flatten(1))