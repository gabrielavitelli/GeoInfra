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
# CMIP6
print ("---------------------------- CMIP6 ----------------------------")
# meu pc
#entrada_cmpi6="C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/dados/adaptor.esgf_wps.retrieve-1712753939.4928932-5893-18-0ec3dc54-031f-4f3b-9fad-040d0eaec0b4/tas_day_MIROC6_ssp585_r1i1p1f1_gn_20500101-20991231_v20191016.nc"
#ds_cmip6 = xr.open_mfdataset(entrada_cmpi6)
# recorto lat lon tempo
#recorte=ds_cmip6['tas'].isel(lat=0,lon=0, time=slice(0, 30))
#dimensionado = recorte.expand_dims({'lat': [recorte.lat], 'lon': [recorte.lon]})
# cria formato tabela
#df = dimensionado.to_dataframe(name='tas')
#df_CMIP6 = df.drop(labels='height', axis=1)
#print (df_CMIP6.reset_index()) 
# digitar no terminal: python CMIP6.py > CMIP6.txt
# quit()

# lab geoinfra
entrada_miroc6_geoinfra = "C:/Users/Usuario/gabriela/dados/CMIP6/MIRO6/pr_day_MIROC6_ssp585_r1i1p1f1_gn_20500101-20511231_v20191016.nc"
ds_miroc6= xr.open_mfdataset(entrada_miroc6_geoinfra)
miroc6_recortado = converte_coordenada(ds_miroc6)
#print (ds_miroc6['pr'].units) #kg m-2 s-1


# Calculando a resolução para latitude e longitude
lat_res = abs(miroc6_recortado['lat'][1] - miroc6_recortado['lat'][0])
lon_res = abs(miroc6_recortado['lon'][1] - miroc6_recortado['lon'][0])

print(f"Resolução da Latitude: {lat_res} graus")
print(f"Resolução da Longitude: {lon_res} graus")

# cria formato tabela
df = miroc6_recortado.to_dataframe()
df.drop(labels=['lat_bnds', 'lon_bnds', 'time_bnds'], axis=1, inplace=True)
print (df.reset_index()) 
# digitar no terminal: python CMIP6.py > CMIP6.txt


###########################################################################################3

#HadGem
print ("---------------------------- HadGEM3-GC31-MM (UK) ----------------------------")
#entrada_hadgem_note = "C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/dados/adaptor.esgf_wps.retrieve-1712755790.4155934-28912-10-aa6a2da7-076d-4ed9-9962-4c1a13fc44b2/tas_day_HadGEM3-GC31-MM_ssp585_r1i1p1f3_gn_20500101-20991230_v20200515.nc"

#lab geoinfra
#HadGEM3-GC31-MM (UK)
entrada_hadgem = "C:/Users/Usuario/gabriela/dados/CMIP6/HadGem/hadgem/pr_day_HadGEM3-GC31-MM_ssp585_r1i1p1f3_gn_20500101-20511230_v20200515.nc"
ds_hadgem = xr.open_mfdataset(entrada_hadgem)
hadgem_recortado = converte_coordenada(ds_miroc6)

# cria formato tabela
df_hadgem = hadgem_recortado.to_dataframe()
df_hadgem.drop(labels=['lat_bnds', 'lon_bnds', 'time_bnds'], axis=1, inplace=True)
print (df_hadgem.reset_index()) 
# digitar no terminal: python CMIP6.py > CMIP6.txt


# Calculando a resolução para latitude e longitude
lat_res = abs(hadgem_recortado['lat'][1] - hadgem_recortado['lat'][0])
lon_res = abs(hadgem_recortado['lon'][1] - hadgem_recortado['lon'][0])

print(f"Resolução da Latitude: {lat_res} graus")
print(f"Resolução da Longitude: {lon_res} graus")


###########################################################################################3

# CHIRPS
'''
print ("---------------------------- CHIRPS ----------------------------")
#entrada_chirps_note="C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/dados/chirps-v2.0.2024.days_p05.nc"

entrada_chirps_lab = "C:/Users/Usuario/gabriela/dados/CHIRPS/chirps-v2.0.2024.days_p05.nc"
ds_chirps= xr.open_mfdataset(entrada_chirps_lab)
print (ds_chirps)
quit()
chirps_recortado = converte_coordenada(ds_chirps)
print (ds_chirps['pr'].units) #kg m-2 s-1
quit()

# Calculando a resolução para latitude e longitude
lat_res = abs(miroc6_recortado['lat'][1] - miroc6_recortado['lat'][0])
lon_res = abs(miroc6_recortado['lon'][1] - miroc6_recortado['lon'][0])

print(f"Resolução da Latitude: {lat_res} graus")
print(f"Resolução da Longitude: {lon_res} graus")

# cria formato tabela
df = miroc6_recortado.to_dataframe()
df.drop(labels=['lat_bnds', 'lon_bnds', 'time_bnds'], axis=1, inplace=True)
print (df.reset_index()) 
# digitar no terminal: python CMIP6.py > CMIP6.txt
'''
###########################################################################################3


# mapa da localizacao do dado, zoom
m = folium.Map(location=[(miroc6_recortado.lat).mean(), (miroc6_recortado.lon).mean()])

latitude = miroc6_recortado.lat.values
longitude = miroc6_recortado.lon.values
for lat, lon in zip(latitude, longitude):
    folium.Marker([lat, lon]).add_to(m)
for lat in latitude:
    for lon in longitude:
        folium.Marker([lat, lon]).add_to(m)

#m.save("cmip6.html")

# Adicionar um marcador para o local
#folium.Marker([latitude, longitude], popup=f'TAS: {recorte.values}').add_to(m)

   
#plt.show()




###########################################################################################3

# temperatura CMIP6
#temperatura=ds_cmip6['tas'] - 273.15
#media_temporal = (temperatura.mean(dim=['lat', 'lon']))

# temperatura HadGem
#temperatura_hadgem=ds_hadgem['tas'] - 273.15
#media_temporal_hadgem = (temperatura_hadgem.mean(dim=['lat', 'lon']))

# precipitacao CHIRPS
#precipitacao_chirps = chirps_recortado['pr']
#media_temporal_CHIRPS = (precipitacao_chirps.mean(dim=['lat', 'lon']))


####################################################################################################
# gera um mapa para conferir localizacao 

# pega coordenadas do dado
min_latitude, max_latitude = miroc6_recortado.lat.min().values, miroc6_recortado.lat.max().values
min_longitude, max_longitude = miroc6_recortado.lon.min().values, miroc6_recortado.lon.max().values

# retangulo para a area do dado
area = box(min_longitude, min_latitude, max_longitude, max_latitude)
area_dataframe = gpd.GeoDataFrame({'geometry': [area]}, crs=shp_ma.crs)

# plotagem
fig, ax = plt.subplots()
shp_ma.plot(ax=ax, color='blue')  
shp_pa.plot(ax=ax, color='blue')  
area_dataframe.plot(ax=ax, color='red', alpha=0.5)  # Plotar retângulo com transparência
plt.title ("Localização")
plt.show()

###########################################################################################3
# plotagens

# plotagem modelos
#plt.plot(chirps_recortado.time, media_temporal_CHIRPS.values)
#plt.plot(ds_hadgem.time, media_temporal_hadgem.values)

# plotagem localizacao 
#fig, ax = plt.subplots(figsize=(10, 10))  # Ajuste o tamanho conforme necessário
#shp_ma.plot(ax=ax, color='blue', edgecolor='k')  
#shp_pa.plot(ax=ax, color='blue', edgecolor='k')  


#plt.title(f"Série temporal de temperatura para 2050-01-01 a 2099-12-31")
#plt.ylabel('Temperatura (°C)')
#plt.show()


quit()
###########################################################################################3