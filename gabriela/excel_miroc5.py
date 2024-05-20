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
#import cdsapi
'''c = cdsapi.Client()
c.retrieve(
'projections-cordex-domains-single-levels',
{
'format': 'zip',
'domain': 'south_america",
'experiment': 'historical',
'horizontal_resolution': '0_20_degree_x_0_20_degree",
'temporal_resolution': 'daily_mean',
'variable': 'mean_precipitation_flux',
'gcm_model': 'miroc_miroc5",
'rcm_model': 'inpe_eta',
'ensemble_member': 'rlilp1',
'start_year': [
'1960', '1961', '1966',
'1971', '1976', '1981',
'1986', '1991",
],
'end_year': [
'1960', '1965', '1970',
'1975', '1980', '1985',
'1990', '1995', '2000'
],
},
'download.zip')
'''
#####################################################################################################################################
# dados de entrada

#entrada='/mnt/c/Users/Usuario/gabriela/dados/CORDEX/historico/dataset-projections-cordex-domains-single-levels-3896b270-7908-453f-8171-ddedafe35bde/pr_SAM-20_MIROC-MIROC5_historical_r1i1p1_INPE-Eta_v1_day_20010101-20051231.nc'

caminho='/mnt/c/Users/Usuario/gabriela/dados/miroc5/*/*.nc'
arquivos_miroc5 = sorted(glob.glob(caminho))

caminho_regcm4 = '/mnt/usb/RegCM4/*/historical/3hr/pr/*'
arquivos_regcm4 = sorted(glob.glob(caminho_regcm4))

saida='/mnt/c/Users/Usuario/gabriela'


################################################################
def resolucao(dado_bruto):
    
    # Calculando a resolução para latitude e longitude
    lat_res = abs(dado_bruto['lat'][1] - dado_bruto['lat'][0])
    lon_res = abs(dado_bruto['lon'][1] - dado_bruto['lon'][0])
    
    print(f"Resolução da Latitude: {lat_res} graus")
    print(f"Resolução da Longitude: {lon_res} graus")

################################################################
def acum_mensal():
    print ('oi')
    






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


################################################################################################################################################################################################
for entrada in arquivos_miroc5:
    dado_bruto = xr.open_dataset(entrada, engine='netcdf4')

    resolucao(dado_bruto)

    ################################################################
    # csv estacoes 

    estacoes_bruto = gpd.read_file("/mnt/c/Users/Usuario/gabriela/Lat Long Estações.xlsx - Planilha1.csv")
    # tira virgula e poe ponto
    df = estacoes_bruto.applymap(lambda x: x.replace(',', '.') if isinstance(x, str) else x)
    #print (df.Nome[3])
    ################################################################
    
    
    # pegando dados
    for estacao in range(len(df.Nome)):
        dado = df.Nome[estacao]
        print (f'---------------{dado}------------------')
        # recortando dados
        latitude = df.Latitude[estacao]
        longitude = df.Longitude[estacao]
        dado_estacao=dado_bruto.sel(lat=latitude, lon=longitude, method='nearest')
        pr=dado_estacao['pr']*86400
        pr_mmdia =pr.to_dataframe()
        
        
    
        if not os.path.isfile(f'resultados/{dado}.csv'):
            pr_mmdia.to_csv(f'resultados/{dado}.csv', mode='w', header=True, sep=';', columns=['lat', 'lon', 'pr'], decimal=',')
        else:
            pr_mmdia.to_csv(f'resultados/{dado}.csv', columns=['lat', 'lon', 'pr'], mode='a', header=False, sep=';', decimal=',')












