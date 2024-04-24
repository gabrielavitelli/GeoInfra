from cdo import *
import xarray as xr
import matplotlib.pyplot as plt
import numpy as np
import cartopy, cartopy.crs as ccrs  
import cartopy.io.shapereader as shpreader
import cartopy.feature as cfeature
import geopandas as gpd
import pandas as pd
#/mnt/c/Users/Usuario/gabriela/
entrada='/mnt/c/Users/Usuario/gabriela//dados/CORDEX/pr_SAM-20_MIROC-MIROC5_rcp85_r1i1p1_INPE-Eta_v1_day_20960101-20991231.nc'

#print ('xarray')
dado_bruto = xr.open_dataset(entrada)
print (dado_bruto)

#pr = dado_bruto['pr']
#Crie o gráfico
#fig, ax = plt.subplots(subplot_kw={'projection': ccrs.PlateCarree()}, figsize=(6,6))
#ax.contourf(pr.lon, pr.lat, pr[1,:,:], cmap='coolwarm')
#plt.show()
#quit()

Min_Longitude = -50.12517419989768
Min_Latitude = -6.014153889871978
Max_Longitude = -44.29318420965952
Max_Latitude = -2.621573681489544
saida='/mnt/c/Users/Usuario/gabriela'
cdo = Cdo()

print(cdo.version())

tempPath = './tmp/'
cdo = Cdo(tempdir=tempPath)


cdo.cleanTempDir()

#print (cdo.operators)
cdo.debug=True
#print(cdo.sinfon(input=entrada))


#ds = xr.open_dataset(entrada)
#print(ds.info())

cdo.copy(input=entrada, options='-b F64')
#cdo.sinfon(input='outfile.nc')

cdo.mulc(86400, input=entrada, output='mmday.nc')

cdo.selvar('pr', input='mmday.nc', returnXArray='pr', output=f'{saida}/pr.nc')
cdo.seltimestep('1/30', input=f'{saida}/pr.nc', options='-b F64', output=f'{saida}/janeiro.nc')

cdo.sellonlatbox(Min_Longitude,Max_Longitude,Min_Latitude,Max_Latitude, input=f'{saida}/janeiro.nc', output=f'{saida}/area.nc')

cdo.remapbil ('r360x180', input=f'{saida}/area.nc', output=f'{saida}/interpolacao.nc')


data = xr.open_dataset(f'{saida}/area.nc')
#plt.figure(figsize=(10, 5))
fig, ax = plt.subplots(figsize=(50, 50), subplot_kw={'projection': ccrs.PlateCarree()})
#ax.add_feature(cartopy.feature.BORDERS, linestyle='-', linewidth=1)
#ax.set_extent([Min_Longitude, Max_Longitude, Min_Latitude, Max_Latitude], crs=ccrs.PlateCarree())

#ax.coastlines()



caminho_geoinfra = "/mnt/c/Users/Usuario/gabriela/dados/shp/"

shp_caminho_pa = f'{caminho_geoinfra}PA_Municipios_2022/PA_Municipios_2022.shp'
shp_pa= gpd.read_file(shp_caminho_pa)
shp_caminho_ma = f'{caminho_geoinfra}MA_Municipios_2022/MA_Municipios_2022.shp'
shp_ma=gpd.read_file(shp_caminho_ma)
# efc #
shp= gpd.read_file("/mnt/c//Users/Usuario/gabriela/dados/shp/EFC/extensao_efc_shp.shp")


ax.set_xlim([shp_ma.bounds.minx.min(), shp_ma.bounds.maxx.max()])
ax.set_ylim([shp_ma.bounds.miny.min(), shp_ma.bounds.maxy.max()])
data['pr'].isel(time=0).plot(ax=ax, transform=ccrs.PlateCarree())

# plotagem
shp.plot(ax=ax)
shp_ma.plot(ax=ax, facecolor='none', linewidth=3) 
shp_pa.plot(ax=ax, facecolor='none', linewidth=3)
#area = box(min_longitude, min_latitude, max_longitude, max_latitude)
#area_dataframe = gpd.GeoDataFrame({'geometry': [area]}, crs=shp_ma.crs)
#area_dataframe.plot(ax=ax, color='red', alpha=0.5)  # Plotar retângulo com transparência
#area_dataframe_cordex.plot(ax=ax, color='yellow', alpha=0.5)  # Plotar retângulo com transparência
plt.show()
quit()


########### ate aqui dados de janeiro no recorte da EFC para precipitacao #######




#plt.ylabel('Precipitacao (mm/dia)')
plt.show()



quit()
