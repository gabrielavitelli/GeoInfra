import pandas as pd
import os
from glob import glob
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
import matplotlib.dates as mdates
import rioxarray
from shapely.geometry import mapping
import geopandas as gpd
import pandas as pd
###########################################################################################3



###########################################################################################3
# pega os dados do computador fujita para o ano de 2014 #
# 20141*1*
saida='/mnt/c/Users/Usuario/gabriela'

shp= gpd.read_file("/mnt/c/Users/Usuario/gabriela/dados/shp/EFC/extensao_efc_shp.shp", crs="epsg:4326")
entrada='/mnt/c/Users/Usuario/gabriela/dados/CORDEX/historico/dataset-projections-cordex-domains-single-levels-3896b270-7908-453f-8171-ddedafe35bde/tas_SAM-20_MIROC-MIROC5_historical_r1i1p1_INPE-Eta_v1_day_20010101-20051231.nc'
coletanea = sorted(glob(entrada))
ds = xr.open_mfdataset(coletanea)

###########################################################################################3

data = ds.rio.set_spatial_dims(x_dim="lon", y_dim="lat", inplace=True)
#  buffer em torno da linha 
buffer = shp.buffer(0.5)
# Converter o xarray dataset para um rioxarray para facilitar a operação de corte
data_rio = data.rio.write_crs("EPSG:4326")  
# Recortar o dataset usando o buffer
clipped_ds = data_rio['tas'].rio.clip(buffer.geometry, buffer.crs)
# Salvar o resultado ou fazer mais operações
clipped_ds.to_netcdf("saida_clipped.nc")
recortado= xr.open_dataset(f'{saida}/saida_clipped.nc')

tempo = ds.time.data
tempo_configurado = pd.to_datetime(tempo)
dia_juliano = tempo_configurado.dayofyear
temperatura=recortado['tas'] - 273.15
#print (temperatura.latitude)

media_temporal =temperatura.mean(dim=['lat', 'lon'])



###########################################################################################3

plt.plot(dia_juliano.astype(str).to_numpy(), media_temporal.to_numpy())#, marker='o',linestyle='None')

plt.title(f"Série temporal de temperatura para 2001-01-01 a 2005-12-31 para area da efc, dados históricos do MG CORDEX e MR MIROC-MIROC5")
plt.ylabel('Temperatura (°C)')

plt.show()
quit()
    #plt.savefig(f"/home/gabriela/treinamento/python/scripts/2014/figuras/{estacao}.jpeg", format ='jpeg')
    #plt.close()

###########################################################################################3






###########################################################################################3
###########################################################################################3
###########################################################################################3
###########################################################################################3

