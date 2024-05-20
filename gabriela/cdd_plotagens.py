import pandas as pd
import os
from glob import glob
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.dates as mdates
import geopandas as gpd
import folium
from shapely.geometry import box
import numpy
####################################################################################################
# no meu github tem o lat_lon_efc.py que pega as duas camadas de linha kml que transformei em shp e 
# usa .bounds do geopandas para extrair coordenadas

Min_Longitude = -50.12517419989768
Min_Latitude = -6.014153889871978
Max_Longitude = -44.29318420965952
Max_Latitude = -2.621573681489544

####################################################################################################
# FUNCAO CONVERTE COORDENADA

def converte_coordenada(dado):

# converte de 0 a 360 para -180 a 180
	dado = dado.assign_coords(lon=(((dado.lon + 180) % 360) - 180))

	# coloca a coordenada na ordem crescente
	dado= dado.sortby('lon')
	dado= dado.sortby('lat')

	## seleciona as variáveis que serão plotadas
	dado_sel=dado.sel(lat=slice(Min_Latitude, Max_Latitude), lon=slice(Min_Longitude, Max_Longitude))

	return dado_sel

####################################################################################################
###########################################################################################3
# kml
#kml_caminho = "C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/referencias/localização/Expansao_EFC.kml"
#kml = gpd.read_file(kml_caminho) 

#shp
#meu pc
caminho_note = "C:/Users/gabri/OneDrive/ESTUDOS/DADOS/"
caminho_geoinfra = "C:/Users/Usuario/gabriela/dados/shp/"

shp_caminho_pa = f'{caminho_geoinfra}PA_Municipios_2022/PA_Municipios_2022.shp'
shp_pa=gpd.read_file(shp_caminho_pa)
shp_caminho_ma = f'{caminho_geoinfra}MA_Municipios_2022/MA_Municipios_2022.shp'
shp_ma=gpd.read_file(shp_caminho_ma)


###########################################################################################3
#CORDEX dados diários
#dados: https://cds.climate.copernicus.eu/cdsapp#!/dataset/projections-cordex-domains-single-levels?tab=overview
'''Domain:
South America
Experiment:
RCP 8.5
Horizontal resolution:
0.20 degree x 0.20 degree
Temporal resolution:
Daily mean
Variable:
2m air temperature
Global climate model:
MIROC-MIROC5 (Japan)
Regional climate model:
INPE-Eta (Brasil)
Ensemble member:
r1i1p1
Start year:
2096
End year:
2099'''
#cordex 
print ("---------------------------- CORDEX CDD e ECACDD----------------------------")
entrada_cordex = "C:/Users/Usuario/gabriela/dados/CORDEX/cdd_ecacdd.nc"
ds_cordex = xr.open_mfdataset(entrada_cordex)
print (ds_cordex['consecutive_dry_days_index_per_time_period'].values)
print (ds_cordex['number_of_cdd_periods_with_more_than_5days_per_time_period'].values)
cdd_index=ds_cordex['consecutive_dry_days_index_per_time_period'].values
print(f'Média: {cdd_index.mean()}')
print(f'Variância: {cdd_index.var()}')

quit()
plt.plot(ds_cordex['consecutive_dry_days_index_per_time_period'].values)#, marker='o',linestyle='None')
plt.show()
quit()


cordex_recortado = converte_coordenada(ds_cordex)
print (cordex_recortado)

# cria formato tabela
df_cordex = cordex_recortado.to_dataframe()
tabela_cordex=df_cordex.reset_index()
pd.set_option('display.max_rows', None)
#tabela_cordex.to_csv('cordex.csv', index=False, sep=';')
#tabela_cordex.to_csv('cordex.txt', index=False, sep='\t')

# Calculando a resolução para latitude e longitude
lat_res = abs(cordex_recortado['lat'][1] - cordex_recortado['lat'][0])
lon_res = abs(cordex_recortado['lon'][1] - cordex_recortado['lon'][0])

print(f"Resolução da Latitude: {lat_res} graus")
print(f"Resolução da Longitude: {lon_res} graus")

# mapa da localizacao do dado, zoom
m = folium.Map(location=[(cordex_recortado.lat).mean(), (cordex_recortado.lon).mean()])

latitude = cordex_recortado.lat.values
longitude = cordex_recortado.lon.values
for lat, lon in zip(latitude, longitude):
    folium.Marker([lat, lon]).add_to(m)
for lat in latitude:
    for lon in longitude:
        folium.Marker([lat, lon]).add_to(m)

m.save("cordex.html")

# Adicionar um marcador para o local
folium.Marker([latitude, longitude], popup=f'TAS: {recorte.values}').add_to(m)

   
#plt.show()



###########################################################################################3

# gera um mapa para conferir localizacao 

# pega coordenadas do dado
min_latitude, max_latitude = cordex_recortado.lat.min().values, cordex_recortado.lat.max().values
min_longitude, max_longitude = cordex_recortado.lon.min().values, cordex_recortado.lon.max().values

# retangulo para a area do dado
area_cordex = box(min_longitude, min_latitude, max_longitude, max_latitude)
area_dataframe_cordex = gpd.GeoDataFrame({'geometry': [area_cordex]}, crs=shp_ma.crs)

# pega coordenadas do dado
min_latitude, max_latitude = cordex_recortado.lat.min().values, cordex_recortado.lat.max().values
min_longitude, max_longitude = cordex_recortado.lon.min().values, cordex_recortado.lon.max().values

# retangulo para a area do dado
area = box(min_longitude, min_latitude, max_longitude, max_latitude)
area_dataframe = gpd.GeoDataFrame({'geometry': [area]}, crs=shp_ma.crs)



# plotagem
fig, ax = plt.subplots()
shp_ma.plot(ax=ax, color='blue')  
shp_pa.plot(ax=ax, color='blue')  
area_dataframe.plot(ax=ax, color='red', alpha=0.5)  # Plotar retângulo com transparência
area_dataframe_cordex.plot(ax=ax, color='yellow', alpha=0.5)  # Plotar retângulo com transparência
plt.title ("Localização")
plt.show()

###########################################################################################3
# plotagens

# plotagem modelos
#plt.plot(chirps_recortado.time, media_temporal_CHIRPS.values)
#plt.plot(ds_cordex.time, media_temporal_cordex.values)

# plotagem localizacao 
#fig, ax = plt.subplots(figsize=(10, 10))  # Ajuste o tamanho conforme necessário
#shp_ma.plot(ax=ax, color='blue', edgecolor='k')  
#shp_pa.plot(ax=ax, color='blue', edgecolor='k')  


#plt.title(f"Série temporal de temperatura para 2050-01-01 a 2099-12-31")
#plt.ylabel('Temperatura (°C)')
#plt.show()


quit()
###########################################################################################3
