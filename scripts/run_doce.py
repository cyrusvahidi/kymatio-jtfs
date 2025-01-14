import doce
from time import sleep
from pandas import DataFrame
import time
import numpy as np
from pathlib import Path

import fire
import os
import gin

from train_cnn import run_train

if __name__ == "__main__":
  doce.cli.main()

def set(args):
  experiment = doce.Experiment(
    name = 'jtfs-cnn-std',
    purpose = 'Medley DB Solos Classification with Hybrid Time-Frequency Scattering Networks',
    author = 'Cyrus Vahidi',
    address = 'c.vahidi@qmul.ac.uk',
    version = '0.1'
  )

  experiment.setPath('output', 'results/'+experiment.name+'/')

  experiment.addPlan('cqt',
                     feature = ['cqt'])
  experiment.addPlan('scattering',
                     feature = ['jtfs_3D_J12', 'scat1d'],
                     c = np.array([1e-1, 1e-2, 1e-3]),
                     learn_adalog = [0, 1], 
                     std = [0, 1])
  experiment.setMetrics(
    acc = ['mean*%', 'std%'],
    acc_instruments = ['mean*%', 'std%'],
  )

  experiment.nb_runs = 10
  experiment.n_classes = 8
  experiment._display.metricPrecision = 4

  experiment._display.bar = False

  return experiment

def step(setting, experiment):
  # if os.path.exists(experiment.path.output+setting.id()+'_acc.npy'):
    # return
  # setting_acc = np.zeros((experiment.nb_runs, ))
  setting_acc_macro = np.zeros((experiment.nb_runs, ))
  setting_acc_instruments = np.zeros((experiment.nb_runs, experiment.n_classes))
  setting_val_acc = np.zeros((experiment.nb_runs, ))
  setting_val_loss = np.zeros((experiment.nb_runs, ))

  tic = time.time()
  print(setting.id())

  gin_config_path = preprocess_gin_file(setting)

  for i in range(experiment.nb_runs):
    results = run_train(gin_config_file=gin_config_path)
    # setting_acc[i] = results['acc']
    setting_acc_macro[i] = results['acc_macro']
    setting_acc_instruments[i] = np.array(results['acc_classwise'])

    setting_val_acc[i] = results['val_acc']
    setting_val_loss[i] = results['val_loss']

  # np.save(experiment.path.output+setting.id()+'_acc.npy', setting_acc)
  np.save(experiment.path.output+setting.id()+'_acc_instruments.npy', setting_acc_instruments)
  
  np.save(experiment.path.output+setting.id()+'_acc_macro.npy', setting_acc_macro)
  np.save(experiment.path.output+setting.id()+'_val_acc.npy', setting_val_acc)
  np.save(experiment.path.output+setting.id()+'_val_loss.npy', setting_val_loss)

  duration = time.time()-tic
  np.save(experiment.path.output+setting.id()+'_duration.npy', duration)


def preprocess_gin_file(setting, 
                        gin_base ='gin/doce/config.gin',
                        gin_temp = 'gin/doce/setting.gin'):
  gin_base = os.path.join(os.getcwd(), gin_base)
  gin_temp = os.path.join(os.getcwd(), gin_temp)
  
  if setting.feature == 'cqt':
    setting.c = None
    setting.learn_adalog = None

  config = [f'MedleyDataModule.feature = \'{setting.feature}\'',
            f'MedleySolosClassifier.feature = \'{setting.feature}\'',
            f'MedleySolosClassifier.c = {setting.c}',
            f'MedleySolosClassifier.learn_adalog = {setting.learn_adalog}',
            f'MedleySolosClassifier.std = {setting.std}']

  open(gin_temp, 'w').close() # clear temp
  with open(gin_base,'r') as f_template, open(gin_temp,'a') as f_temp:
    # write template to temp
    for line in f_template:
      f_temp.write(line + '\n')
    # write config
    for line in config:
      f_temp.write(line + '\n')

  feature = setting.feature.split('_')[0]
  gin_config_path = os.path.join(os.getcwd(), f'gin/doce/{feature}.gin')
  # gin.parse_config_file(gin_config_path)
  return gin_config_path
