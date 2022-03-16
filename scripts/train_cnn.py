import gin, os
import pytorch_lightning as pl, fire
from pytorch_lightning import Trainer
from pytorch_lightning.callbacks.early_stopping import EarlyStopping
from pytorch_lightning.loggers import WandbLogger

from kymjtfs.cnn import MedleySolosClassifier, MedleyDataModule

import warnings
warnings.filterwarnings("ignore")

def run_train(n_epochs = 20, 
              batch_size = 32,
              epoch_size = 8192,
              use_cqt = False,
              gin_config_file = 'scripts/gin/config.gin'):
    gin.parse_config_file(os.path.join(os.getcwd(), gin_config_file))
    
    early_stop_callback = EarlyStopping(monitor="val/loss_epoch", 
                                        min_delta=0.00, 
                                        patience=5, 
                                        verbose=True, 
                                        mode="min")
    wandb_logger = WandbLogger()
    trainer = pl.Trainer(gpus=-1, 
                        max_epochs=n_epochs,
                        progress_bar_refresh_rate=1, 
                        enable_checkpointing=True,
                        # callbacks=[early_stop_callback],
                        # fast_dev_run=True,
                        limit_train_batches=epoch_size // batch_size,
                        logger=wandb_logger)
    model = MedleySolosClassifier(use_cqt=use_cqt)
    dataset = MedleyDataModule(batch_size=batch_size, use_cqt=use_cqt) 
    trainer.fit(model, dataset)
    trainer.test(model, dataset)


def main():
  fire.Fire(run_train)


if __name__ == "__main__":
    main()