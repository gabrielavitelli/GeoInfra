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
# caminhos #

# caminho do miroc5
caminho_miroc5 = '/mnt/c/Users/Usuario/gabriela/dados/miroc5/*/*.nc'
arquivos_miroc5 = sorted(glob.glob(caminho_miroc5))

# caminhos do miroc6 resolucao original
caminho_miroc6 = '/mnt/c/Users/Usuario/gabriela/dados/MIROC6/pr_day_MIROC6_historical_r1i1p1f1_gn_19500101-20141231_v20191016.nc'

# caminhos do miroc6 resolucao 0.25 depois do regrid
miroc6_19502014_025 = '/mnt/c/Users/Usuario/gabriela/dados/resolucao/cmip6_latlon.nc'

# caminhos regCM4
caminho_regcm4 = '/mnt/c/Users/Usuario/gabriela/dados/1981-2001/regcm4'
arquivos_regcm4 = sorted(glob.glob(caminho_regcm4))

# caminhos MERGE
caminho_merge = '/mnt/c/Users/Usuario/gabriela/dados/MERGE/*/*/*.grib2'
arquivos_merge = sorted(glob.glob(caminho_merge))

# caminhos ERA5
caminho_era5 = '/mnt/c/Users/Usuario/gabriela/era5_1950_1960.grib'
#era5 = xr.open_dataset(caminho_era5, engine='cfgrib')

################################################################################################################################################################################################

def info (ds):
    try:
        # intervalo temporal
        tmax = min(ds.time.values)
        tmin = max(ds.time.values)
        # resolucao espacial
        lat_res = abs(ds['lat'][1] - ds['lat'][0])
        lon_res = abs(ds['lon'][1] - ds['lon'][0])
        return tmax, tmin, lat_res, lon_res
    except:
        print ('nao consegui obter informacoes do arquivo')
################################################################################################################################################################################################
def resolucao ():
    
    # Carregar o arquivo NetCDF
    ds = xr.open_dataset(caminho_miroc6, chunks={'time': 100})
    
    # Definir a nova grade
    new_lat = np.arange(-49.75, 18.95, 0.25)  # Min, Max, Incremento para latitude
    new_lon = np.arange(270.0, 329.15, 0.25)  # Min, Max, Incremento para longitude
    
    # Criar um novo dataset com a nova grade
    new_ds = xr.Dataset({
        'lat': (['lat'], new_lat),
        'lon': (['lon'], new_lon),
    })
    
    # Interpolar os dados para a nova grade
    regridded_ds = ds.interp(lat=new_ds.lat, lon=new_ds.lon, method='linear')
    
    # Salvar o novo dataset em um arquivo NetCDF
    regridded_ds.to_netcdf(miroc6_19502014_025)

################################################################################################################################################################################################
def gera_csv_cmip6 (pr):
        
        #ds = xr.open_dataset(miroc6_19502014_025, engine='netcdf4')
        #pr=dado_estacao['pr'] #*86400
        #pr_mmdia =pr.to_dataframe()
        #pr['time'] = pd.to_datetime(pr['time'])
        pr['time'] = pr['time'].dt.strftime('%d/%m/%Y %H:%M:%S')
       # pr_mm_dia = pr.to_dataframe(name="precipitacao")
        pr_mmdia = pd.DataFrame({
    'time': pr.coords['time'].values,
    'lat': pr.coords['lat'].values,
    'lon': pr.coords['lon'].values,
    'pr': pr.values
})
        
        if not os.path.isfile(f'resultados/cmip6_{dado}.csv'):
            pr_mmdia.to_csv(f'resultados/modelos/merge_{dado}.csv', mode='w', header=True, sep=';', columns=['time', 'lat', 'lon', 'pr'], decimal=',')
        else:
            pr_mmdia.to_csv(f'resultados/modelos/merge_{dado}.csv', columns=['time', 'lat', 'lon', 'pr'], mode='a', header=True, sep=';', decimal=',')




################################################################################################################################################################################################

def gera_csv_merge (pr):
    ds['longitude']=ds['longitude']-360
    pr = dado_estacao['prec']
    time = pr.coords['time'].values.flatten()
    lat = pr.coords['latitude'].values.flatten()
    lon = pr.coords['longitude'].values.flatten()
    prec = pr.values#.flatten()
    
    index = range(len(ds.coords['latitude'].values.flatten()))
    
    pr_mmdia = pd.DataFrame({'time':time, 'latitude': lat, 'longitude': lon, 'prec': prec }, index=index)
    if not os.path.isfile(f"resultados/novas_estacoes/teste/merge_{nome_estacao}.csv"):
        pr_mmdia['latitude'] = pr_mmdia['latitude'].map(lambda x: format(x, '.5f'))
        pr_mmdia.to_csv(f'resultados/novas_estacoes/teste/merge_{nome_estacao}.csv', mode='w', header=True, sep=';', columns=['time', 'latitude', 'longitude', 'prec'], decimal=',')
        pr_mmdia.to_csv(f'resultados/novas_estacoes/teste/merge_{nome_estacao}.txt', header=True, sep='\t', columns=['time', 'latitude', 'longitude', 'prec'], decimal=',')
    
    else:
        pr_mmdia['latitude'] = pr_mmdia['latitude'].map(lambda x: format(x, '.5f'))
        pr_mmdia.to_csv(f'resultados/novas_estacoes/teste/merge_{nome_estacao}.csv', columns=['time', 'latitude', 'longitude', 'prec'], mode='a', header=False, sep=';', decimal=',')
        pr_mmdia.to_csv(f'resultados/novas_estacoes/teste/merge_{nome_estacao}.txt', columns=['time', 'latitude', 'longitude', 'prec'], mode='a', header=False, sep='\t', decimal=',')


######################################################################################################################################################
# csv lat lon estacao

# csv estacoes 
estacoes_bruto = gpd.read_file("/mnt/c/Users/Usuario/gabriela/dados/latlon.csv") 
# tira virgula e poe ponto
df = estacoes_bruto.applymap(lambda x: x.replace(',', '.') if isinstance(x, str) else x)

################################################################################################################################################################################################
# chama funcao para mudar resolucao dos dados para 0.25
#resolucao()

################################################################################################################################################################################################
# le arquivo nc
#regcm4 = xr.open_dataset(arquivos_regcm4)
# informacoes a respeito do arquivo
# #informacoes=info(ds)
#print (f'\n--------------------------\nintervalo de tempo: {informacoes[0]}  -  {informacoes[1]}\n\nresolucao espacial:\n{informacoes[2]}\n{informacoes[3]}\n--------------------------\n')


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
        
        # recortando o dado a partir dos pontos do nome da estacao
        dado_estacao=ds.sel(latitude=latitude, longitude=longitude, method='nearest')

        gera_csv_merge(dado_estacao)    
        ds.close()
################################################################################################################################################################################################################################################################

quit()









'''
    #ds = xr.open_dataset(entrada)#, engine='netcdf4')
    #merge = xr.open_dataset(entrada) # prec
    #pr_miroc6=ds['pr']*86400
    #print (ds['prec'].values)
   

'''