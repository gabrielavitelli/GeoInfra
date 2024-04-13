import pandas as pd
import os
from glob import glob
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
import matplotlib.dates as mdates
#import netCDF4
# abrindo os dados
#entrada="C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/dados/teste.nc4"

# precisa de uma parte do script que baixe os dados # 


# esse é um xarray de 4 dimensões, eu congelo na linha 34 uma dimensão, se for 3 dimensões é só excluir 'bnds=0', se for nivel isobarico ou outra tem que congelar ou iterar sobre ela
# Selecionando lat lon para Açailandia, Maranhão
lat_especifica = -27.595
lon_especifica = 311.452 
dados_florianopolis = ds.sel(lat=lat_especifica, lon=lon_especifica, method='nearest')

# no caso os dados estavam em kg_m2_s e quero transformar em mm/dia
dados_convertidos = dados_florianopolis['pr'] * 86400
# no dado que peguei tinha dois horários por dia: 00 e 12, tiro a média por dia ('D')
media_diaria=dados_convertidos.resample(time='ME').mean()

np.set_printoptions(threshold=np.inf)


np.set_printoptions(threshold=np.inf)
#print(ds['pr'].values)

###############################################################################################
# CDD #
# numero maximo de dias secos consecutivos no periodo analisado (chuva <1mm)

# contadores
dias_secos = 0
max_dias_secos = 0
intervalo_tempo = (None, None)
seq = None
# itera no tempo, para todas lat lon
for tempo in range(len(media_diaria['time'])):
    # verifica se trata-se de uma estiagem
    #print(media_diaria.isel(bnds=0, time=tempo)['pr'].values)
    if ((media_diaria.isel(time=tempo)< 1).all()):
        dias_secos+=1
     #   print (dias_secos)
        if seq is None: # achou o início de uma seca
            seq=media_diaria['time'][tempo].values
        
    else: # choveu
        if dias_secos > 0 and dias_secos > max_dias_secos:
            # agora que acabou período de estiagem atualiza o intervalo max
            max_dias_secos = dias_secos
            intervalo_tempo=(seq, media_diaria['time'][tempo-1].values)
      #      print (f"max:{max_dias_secos}")
        dias_secos=0
        seq=None
if dias_secos > 0 and dias_secos > max_dias_secos:
        max_dias_secos = dias_secos
        intervalo_tempo = (seq, media_diaria['time'][-1].values)
    
print (f"CDD: {max_dias_secos}")

print(f"Período de seca de {intervalo_tempo[0]} até {intervalo_tempo[1]}")


###############################################################################################
# CWD #
# numero maximo de dias consecutivos com chuva no periodo analisado (chuva>=1mm)

# contadores
dias_chuvosos = 0
max_dias_chuvosos = 0
intervalo_tempo = (None, None)
seq = None

# itera no tempo, para todas lat lon
for tempo in range(len(media_diaria['time'])):
    # verifica se trata-se de uma estiagem
    if ((media_diaria.isel(time=tempo) >= 1).all()):
        dias_chuvosos+=1
        #print ("choveu")
        if seq is None: # achou o início de uma seca
            seq=media_diaria['time'][tempo].values
        
    else: 
        if dias_chuvosos > 0 and dias_chuvosos > max_dias_chuvosos:
            # agora que acabou período de estiagem atualiza o intervalo max
            max_dias_chuvosos = dias_chuvosos
            intervalo_tempo=(seq, media_diaria['time'][tempo-1].values)
            
        dias_chuvosos=0
        seq=None
if dias_chuvosos > 0 and dias_chuvosos > max_dias_chuvosos:
        max_dias_chuvosos = dias_chuvosos
        intervalo_tempo = (seq, media_diaria['time'][-1].values)
    
print (f"CWD: {max_dias_chuvosos}")

print(f"Período de chuva de {intervalo_tempo[0]} até {intervalo_tempo[1]}")


quit()