# -*- coding: utf-8 -*-
"""
Created on Tue May  7 10:14:34 2024

@author: gabriela
"""
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
from sympy.physics import units
import dask

#####################################################################################################################################
# dados de entrada

#entrada='/mnt/c/Users/Usuario/gabriela/dados/CORDEX/historico/dataset-projections-cordex-domains-single-levels-3896b270-7908-453f-8171-ddedafe35bde/pr_SAM-20_MIROC-MIROC5_historical_r1i1p1_INPE-Eta_v1_day_20010101-20051231.nc'

caminho='/mnt/c/Users/Usuario/gabriela/dados/miroc5/*/*.nc'
arquivos_miroc5 = sorted(glob.glob(caminho))

#caminho_regcm4 = '/mnt/usb/RegCM4/*/historical/3hr/pr/*'
caminho_regcm4 = '/mnt/c//Users/Usuario/gabriela/dados/1981-2001/regcm4/*'
arquivos_regcm4 = sorted(glob.glob(caminho_regcm4))

caminho_chirps = '/mnt/c//Users/Usuario/gabriela/dados/CHIRPS/chirps*'
arquivos_chirps= sorted(glob.glob(caminho_chirps))

saida='/mnt/c/Users/Usuario/gabriela'


################################################################
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
    
    ds = xr.open_dataset(miroc6_19502014_025, chunks={'time': 100})
    # Calculando a resolução para latitude e longitude
    lat_res = abs(dado_bruto['lat'][1] - dado_bruto['lat'][0])
    lon_res = abs(dado_bruto['lon'][1] - dado_bruto['lon'][0])
    
    print(f"Resolução da Latitude: {lat_res} graus")
    print(f"Resolução da Longitude: {lon_res} graus")
    
################################################################
def shapefiles():
    # arquivos de entrada
    
    # shapefiles #
    caminho_geoinfra = "/mnt/c/Users/Usuario/gabriela/dados/shp/"
    shp_caminho_pa = f'{caminho_geoinfra}PA_Municipios_2022/PA_Municipios_2022.shp'
    shp_pa= gpd.read_file(shp_caminho_pa)
    shp_caminho_ma = f'{caminho_geoinfra}MA_Municipios_2022/MA_Municipios_2022.shp'
    shp_ma=gpd.read_file(shp_caminho_ma)
    # efc #
    shp= gpd.read_file("/mnt/c/Users/Usuario/gabriela/dados/shp/EFC/extensao_efc_shp.shp", crs="epsg:4326")


################################################################
def chirps():
    dados_chirps=[]
    for entrada in arquivos_chirps:
        dado_bruto = xr.open_dataset(entrada, chunks={'time': 100})
        print (dado_bruto)
        quit()
        extend = [-90, -30, -49.97, 20] # Min lon, Max lon, Min lat, Max lat
        dado_sam=dado_bruto.sel(latitude=slice(extend[2], extend[3]), longitude=slice(extend[0], extend[1]))
        dados_chirps.append(dado_sam)
        lat_res = abs(dado_bruto['latitude'][1] - dado_bruto['latitude'][0])
        lon_res = abs(dado_bruto['longitude'][1] - dado_bruto['longitude'][0])

    print(f"Resolução da Latitude: {lat_res} graus")
    print(f"Resolução da Longitude: {lon_res} graus")
    #quit()

    dados_concatenados = xr.concat(dados_chirps, dim='time')
    
    precipitacao_mensal = dados_concatenados['precip'].resample(time='1M').sum()
    # agora temos precipitacao mensal
    media_mensal = precipitacao_mensal.groupby('time.month').mean(dim='time')
    print (media_mensal[1,:,:]) #fevereiro
    plt.figure(figsize=(10, 6))
    media_mensal_mensal = media_mensal.mean(dim=['latitude', 'longitude'])
    for mes in range(1, 13):
        media_mensal_mes = media_mensal.sel(month=mes)
        media_mensal_mes = media_mensal_mes.compute()
        #print(f'Média mensal para o mês {mes}: {media_mensal_mes.values}')
        media_mensal_mes.to_netcdf(f'/mnt/e/GeoInfra/dados/chirps_media_accum_mensal_1981_2001/chirps_1981_2001_media_accum_mensal_{mes:02d}.nc')
    quit()

##############################################################################################################################################################################################
# recortando para SAM
# mudando de  kg m-2 s-1 para mm/dia
def miroc5():
    
    extend = [-90, -30, -50, 20] # Min lon, Max lon, Min lat, Max lat
    for entrada in arquivos_miroc5:
        dado_bruto = xr.open_dataset(entrada, engine='netcdf4')
        dado_sam=dado_bruto.sel(lat=slice(extend[2], extend[3]), lon=slice(extend[0], extend[1]))
        pr=dado_sam['pr']*86400  # convertendo de kg m-2 s-1 para mm/dia
        #acm_mensal = 
        
    resolucao(dado_bruto)



################################################################
# já esta recortado SAM

def RegCM4():
    dados_regcm4=[]
    for entrada in arquivos_regcm4:
        dado_bruto = xr.open_dataset(entrada, chunks={'time': 100})
        #dado_recortado = dado_bruto.sel(time=slice('1980', '2000'))
        # Todos os arquivos foram lidos. #

        dado_bruto['pr'] = dado_bruto['pr']*86400  # convertendo de kg m-2 s-1 para mm/dia
        dados_regcm4.append(dado_bruto)
        # Todos os arquivos foram processados e convertidos para mm/dia. #
    
        
    # agora já temos dados_regcm4 com mm/dia
    #resolucao(dado_bruto)
    dados_concatenados = xr.concat(dados_regcm4, dim='time')
    
    #print (dados_concatenados['pr'].values)
    precipitacao_mensal = dados_concatenados['pr'].resample(time='1M').sum()
    #print(f'Valores de precipitação mensal (primeiros 10 valores): {precipitacao_mensal.isel(time=slice(0, 10)).values}')

    
    # agora temos precipitacao mensal
    media_mensal = precipitacao_mensal.groupby('time.month').mean(dim='time')
    #print (media_mensal.values)

      # Passo 6: Visualização dos Dados
    plt.figure(figsize=(10, 6))
    media_mensal_mensal = media_mensal.mean(dim=['y', 'x'])
    media_mensal_mensal.plot()
    plt.title('Média Mensal de Precipitação Acumulada (1980-2005)')
    plt.xlabel('Mês')
    plt.ylabel('Precipitação (mm/mês)')
    plt.grid(True)
    plt.show()
    for mes in range(1, 13):
        media_mensal_mes = media_mensal.sel(month=mes)
        media_mensal_mes = media_mensal_mes.compute()
        #print(f'Média mensal para o mês {mes}: {media_mensal_mes.values}')
        media_mensal_mes.to_netcdf(f'regcm4_1980_2005_media_mensal_{mes:02d}.nc')

    


################################################################
def merge ():
    
# -49.975
    extend = [20, -30, -50, -90] # Min lon, Max lon, Min lat, Max lat


################################################################

# serie temporal: 1981-2001
chirps()
#miroc5()
#RegCM4()

    
quit()

























