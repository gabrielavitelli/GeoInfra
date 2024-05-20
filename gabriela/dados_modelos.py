# 09:12 12/04/2024

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
###########################################################################################3
# kml
#kml_caminho = "C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/referencias/localizacao/Expansao_EFC.kml"
#kml = gpd.read_file(kml_caminho) 

#shp
#shp_caminho_pa = "C:/Users/gabri/OneDrive/ESTUDOS/DADOS/PA_Municipios_2022/PA_Municipios_2022.shp"
#shp_pa=gpd.read_file(shp_caminho_pa)
#shp_caminho_ma = "C:/Users/gabri/OneDrive/ESTUDOS/DADOS/MA_Municipios_2022/MA_Municipios_2022.shp"
#shp_ma=gpd.read_file(shp_caminho_ma)

###########################################################################################3
# CMIP6
'''entrada_cmpi6="C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/dados/adaptor.esgf_wps.retrieve-1712753939.4928932-5893-18-0ec3dc54-031f-4f3b-9fad-40d0eaec0b4/tas_day_MIROC6_ssp585_r1i1p1f1_gn_20500101-20991231_v20191016.nc"
ds_cmip6 = xr.open_mfdataset(entrada_cmpi6)

#HadGem
entrada_hadgem = "C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/dados/adaptor.esgf_wps.retrieve-1712755790.4155934-28912-10-aa6a2da7-076d-4ed9-9962-4c1a13fc44b2/tas_day_HadGEM3-GC31MM_ssp585_r1i1p1f3_gn_20500101-20991230_v20200515.nc"
ds_hadgem = xr.open_mfdataset(entrada_hadgem)
'''

# CHIRPS
entrada_chirps = "C:/Users/Usuario/gabriela/dados/chirps-v2.0.2024.days_p05.nc"
ds_chirps = xr.open_mfdataset(entrada_chirps)
print (ds_chirps)
quit()

# recorto lat lon tempo
recorte=ds_cmip6['tas'].isel(lat=0,lon=0, time=slice(0, 30))
dimensionado = recorte.expand_dims({'lat': [recorte.lat], 'lon': [recorte.lon]})

# crio formato tabela
df = dimensionado.to_dataframe(name='tas')
df_CMIP6 = df.drop(labels='height', axis=1)
#print (df_CMIP6.reset_index()) 
# digitar no terminal: python CMIP6.py > CMIP6.txt

# quit()
###########################################################################################3


# mapa da localizacao do dado, zoom
m = folium.Map(location=[(ds_cmip6.lat).mean(), (ds_cmip6.lon).mean()])

latitude = ds_cmip6.lat.values
longitude = ds_cmip6.lon.values
for lat, lon in zip(latitude, longitude):
    folium.Marker([lat, lon]).add_to(m)

m.save("cmip6.html")
#    folium.Marker([lat, lon], popup=f'TAS: {ds_cmip6['tas'].values}').add_to(m)

# Adicionar um marcador para o local
#folium.Marker([latitude, longitude], popup=f'TAS: {recorte.values}').add_to(m)

#plt.show()
quit()



###########################################################################################3

# temperatura CMIP6
temperatura=ds_cmip6['tas'] - 273.15
media_temporal = (temperatura.mean(dim=['lat', 'lon']))

# temperatura HadGem
temperatura_hadgem=ds_hadgem['tas'] - 273.15
media_temporal_hadgem = (temperatura_hadgem.mean(dim=['lat', 'lon']))

# temperatura CHIRPS

###########################################################################################3
# plotagens

# plotagem modelos
plt.plot(ds_cmip6.time, media_temporal.values)
#plt.plot(ds_hadgem.time, media_temporal_hadgem.values)

# plotagem localizacao 
fig, ax = plt.subplots(figsize=(10, 10))  # Ajuste o tamanho conforme necessário
shp_ma.plot(ax=ax, color='blue', edgecolor='k')  
shp_pa.plot(ax=ax, color='blue', edgecolor='k')  


plt.title(f"Serie temporal de temperatura para 2050-01-01 a 2099-12-31")
plt.ylabel('Temperatura (°C)')
plt.show()


quit()
###########################################################################################3
