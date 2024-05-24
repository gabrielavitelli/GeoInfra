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
from datetime import datetime
import pygrib
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

#caminho_regcm4 = '/mnt/usb/RegCM4/*/historical/3hr/pr/*'
caminho_regcm4 = '/mnt/c/Users/Usuario/gabriela/dados/1981-2001/regcm4'
arquivos_regcm4 = sorted(glob.glob(caminho_regcm4))

caminho_miroc6 = '/mnt/c/Users/Usuario/gabriela/pr_day_MIROC6_historical_r1i1p1f1_gn_19500101-20141231_v20191016_lonlat.nc'  #esse arquivo é de 1.40075354 graus #pr_day_MIROC6_historical_r1i1p1f1_gn_19500101-20141231_v20191016.nc'
arquivos_miroc6 = sorted(glob.glob(caminho_miroc6))

saida='/mnt/c/Users/Usuario/gabriela'


era5 = '/mnt/c/Users/Usuario/gabriela/era5_1950_1960.grib'

print ('oi')
ds = xr.open_dataset(era5, engine='cfgrib')
print (ds)
quit()
################################################################
def resolucao(dado_bruto):
    
    # Calculando a resolução para latitude e longitude
    lat_res = abs(dado_bruto['lat'][1] - dado_bruto['lat'][0])
    lon_res = abs(dado_bruto['lon'][1] - dado_bruto['lon'][0])
    print ('oi')
    print(f"Resolução da Latitude: {lat_res} graus")
    print(f"Resolução da Longitude: {lon_res} graus")

################################################################
'''def acum_mensal(pr, dado_estacao):
    tempo = dado_estacao.time.data
    anos = pd.to_datetime(tempo).year
    anos=sorted(set(anos))
    
    dado_estacao['month'] = dado_estacao['time'].dt.month
    dado_estacao['year'] = dado_estacao['time'].dt.year
    
    precipitacao_acumulada_por_mes = dado_estacao.groupby('time.dt.month').sum('time')
    #precipitacao_acumulada_por_mes = dado_estacao.groupby('month')#.groupby('year').sum()
    print (precipitacao_acumulada_por_mes)
    quit()
    media_precipitacao_mensal = precipitacao_acumulada_por_mes.mean('year')
    
    media_precipitacao_mensal = precipitacao_acumulada.groupby('time.month').mean()
    return media_precipitacao_mensal
    
    precipitacao_acumulada = dado_estacao.groupby('time.dt.month').sum('time')

    # Calcular a média da precipitação acumulada mensal ao longo dos anos
    media_precipitacao_mensal = precipitacao_acumulada.groupby('time.month').mean()
    
    
    
    
    dado_estacao_anual = dado_estacao.groupby(dado_estacao.time.dt.year)
    print (dado_estacao_anual)
   
 
    pr_estacao_acum_mensal = dado_estacao_anual.groupby(dado_estacao_anual.time.dt.month).sum() #me fornece 12 valores para 1 ano pr.time.dt.month
    
    print ('\n\ndados anuais')
    print (dado_estacao_anual)
    
    print ('\n\n somas mensais')
    print (pr_estacao_acum_mensal)
    
    return pr_estacao_acum_mensal
    

        
    #pr=dado_estacao_anual['pr']*86400
    
    #tempo = dado_estacao_anual.time.data
'''

    

################################################################
def gera_csv (pr):
    
        #pr=dado_estacao['pr'] #*86400
        pr_mmdia =pr.to_dataframe()

        if not os.path.isfile(f'resultados/regcm4_{dado}.csv'):
            pr_mmdia.to_csv(f'resultados/regcm4_{dado}.csv', mode='w', header=True, sep=';', columns=['lat', 'lon', 'pr'], decimal=',')
        else:
            pr_mmdia.to_csv(f'resultados/regcm4_{dado}.csv', columns=['lat', 'lon', 'pr'], mode='a', header=False, sep=';', decimal=',')




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
for entrada in arquivos_miroc6:
    print ('oi')
    dado_bruto = xr.open_dataset(entrada, engine='netcdf4')
    tmax = min(dado_bruto.time.values)
    tmin = max(dado_bruto.time.values)
    
    print ('oi')
    resolucao(dado_bruto)
    print (f'{entrada}\n{tmax}\n{tmin}\n{resolucao}')
    quit()
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
        dado_estacao['pr']=dado_estacao['pr']*86400
        pr=dado_estacao['pr']#*86400
        #print (pr)
       # gera_csv(pr)
        #gera_csv(dado_estacao)
        
        #pr_estacao_acum_mensal=acum_mensal(pr, dado_estacao)

        
        # plotando
        plt.plot(pr)
        #plt.plot(range (1, 13), pr.to_numpy())#, marker='o',linestyle='None')
        plt.title(f"Série temporal de precipitação acumulada mm/mes de {tmin} a {tmax}  para {dado}, dados do CORDEX - MIROC-MIROC6")
        #plt.ylabel('Precipitação')
    
        #plt.xticks(np.arange(12, step=1), ['jan', 'fev', 'mar', 'abr', 'maio', 'jun', 'jul', 'agos', 'set', 'out', 'nov', 'dez'], rotation=20)
        #plt.yticks(np.arange(500, step=50))
        plt.show()




#    
'''
    #dado_estacao_anual2 = dado_estacao_anual.groupby('month')['pr'].expanding().mean().reset_index(level=0, drop=True)
    #print (dado_estacao_anual2)
    quit()
    
    #dado_estacao = dado_estacao.sel(time=teste) #f'{ano}'  '{}'.format(ano)
    
    pr=dado_estacao['pr']*86400
    tempo = dado_estacao.time.data

    pr_estacao_acum_mensal=pr.groupby(pr.time.dt.month).sum() #me fornece 12 valores para 1 ano


    #df['media_acumulada'] = df.groupby('ano')['valor'].cumsum() / df.groupby('ano').cumcount()
    #print (pr_estacao_acum_mensal)
    quit()    




'''