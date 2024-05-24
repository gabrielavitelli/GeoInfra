import xarray as xr # uso para abrir o dataset
import numpy as np # mexer com xarray
import dask.array as da # dask
import geopandas as gpd # li csv
from glob import glob
from cdo import *
import xarray as xr
import matplotlib.pyplot as plt
import numpy as np
import cartopy, cartopy.crs as ccrs  
import cartopy.io.shapereader as shpreader
import cartopy.feature as cfeature
import geopandas as gpd
import pandas as pd
import rioxarray
from shapely.geometry import mapping
import csv
import geopandas as gpd
from datetime import datetime
import pygrib
import os
################################################################################################################################################################################################
# caminhos MERGE

caminho_merge = '/mnt/c/Users/Usuario/gabriela/dados/MERGE/*/*/*.grib2'
arquivos_merge = sorted(glob.glob(caminho_merge))


################################################################################################################################################################################################

def gera_csv_merge (pr):
    
    ds['longitude']=ds['longitude']-360 # merge
    
    # recortando o dado a partir dos pontos do nome da estacao
    dado_estacao=ds.sel(latitude=latitude, longitude=longitude, method='nearest')
    
    pr = dado_estacao['prec']
    
    time = pr.coords['time'].values.flatten()
    lat = pr.coords['latitude'].values.flatten()
    lon = pr.coords['longitude'].values.flatten()
    prec = pr.values#.flatten()
    
    index = range(len(time))
    
    pr_mmdia = pd.DataFrame({'time':time, 'latitude': lat, 'longitude': lon, 'prec': prec }, index=index)
    if not os.path.isfile(f"resultados/novas_estacoes/teste/merge_{nome_estacao}.csv"):
        pr_mmdia['latitude'] = pr_mmdia['latitude'].map(lambda x: format(x, '.5f'))
        pr_mmdia['longitude'] = pr_mmdia['longitude'].map(lambda x: format(x, '.5f'))
        pr_mmdia.to_csv(f'resultados/novas_estacoes/teste/merge_{nome_estacao}.csv', mode='w', header=True, sep=';', columns=['time', 'latitude', 'longitude', 'prec'], decimal=',')
        pr_mmdia.to_csv(f'resultados/novas_estacoes/teste/merge_{nome_estacao}.txt', header=True, sep='\t', columns=['time', 'latitude', 'longitude', 'prec'], decimal=',')
    
    else:
        pr_mmdia['latitude'] = pr_mmdia['latitude'].map(lambda x: format(x, '.5f'))
        pr_mmdia['longitude'] = pr_mmdia['longitude'].map(lambda x: format(x, '.5f'))
        pr_mmdia.to_csv(f'resultados/novas_estacoes/teste/merge_{nome_estacao}.csv', columns=['time', 'latitude', 'longitude', 'prec'], mode='a', header=False, sep=';', decimal=',')
        pr_mmdia.to_csv(f'resultados/novas_estacoes/teste/merge_{nome_estacao}.txt', columns=['time', 'latitude', 'longitude', 'prec'], mode='a', header=False, sep='\t', decimal=',')


######################################################################################################################################################
# csv lat lon estacao

# csv estacoes 
estacoes_bruto = gpd.read_file("/mnt/c/Users/Usuario/gabriela/dados/latlon.csv") 
# tira virgula e poe ponto
df = estacoes_bruto.applymap(lambda x: x.replace(',', '.') if isinstance(x, str) else x)

################################################################################################################################################################################################
# pegando dados

for entrada in arquivos_merge:
    print (entrada)
    
    for estacao in range(len(df.Nome)):
        
        # le o netcdf
        ds = xr.open_dataset(entrada, chunks={'time': 'auto'})#, engine='netcdf4')
       
        # descobre nome da estacao do csv
        nome_estacao = df.Nome[estacao]
        
        
        ################################################################################################################################################################################################
        
        
        # pegando lat e lon a partir do csv da estacao dados
        latitude = df.Latitude[estacao]
        longitude = df.Longitude[estacao]
        
        gera_csv_merge(ds)    
        ds.close()
################################################################################################################################################################################################################################################################

quit()




