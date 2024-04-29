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

#####################################################################################################################################
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

saida='/mnt/c/Users/Usuario/gabriela'


#####################################################################################################################################
# shapefiles #
caminho_geoinfra = "/mnt/c/Users/Usuario/gabriela/dados/shp/"
shp_caminho_pa = f'{caminho_geoinfra}PA_Municipios_2022/PA_Municipios_2022.shp'
shp_pa= gpd.read_file(shp_caminho_pa)
shp_caminho_ma = f'{caminho_geoinfra}MA_Municipios_2022/MA_Municipios_2022.shp'
shp_ma=gpd.read_file(shp_caminho_ma)
# efc #
shp= gpd.read_file("/mnt/c/Users/Usuario/gabriela/dados/shp/EFC/extensao_efc_shp.shp", crs="epsg:4326")


#####################################################################################################################################
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
#muda de kg/m2/s para mm/dia
cdo.mulc(86400, input=entrada, output='mmday.nc')
cdo.selvar('pr', input='mmday.nc', returnXArray='pr', output=f'{saida}/pr.nc')
cdo.seltimestep('1/30', input=f'{saida}/pr.nc', options='-b F64', output=f'{saida}/janeiro.nc')
bbox = shp.total_bounds
min_lon, min_lat, max_lon, max_lat = bbox
cdo.sellonlatbox(min_lon, max_lon, min_lat, max_lat, input=f'{saida}/janeiro.nc', output=f'{saida}/area.nc')


data = xr.open_dataset(f'{saida}/area.nc')

#####################################################################################################################################
# mascara #
data = data.rio.set_spatial_dims(x_dim="lon", y_dim="lat", inplace=True)

#  buffer em torno da linha 
buffer = shp.buffer(0.5)
# Converter o xarray dataset para um rioxarray para facilitar a operação de corte
data_rio = data.rio.write_crs("EPSG:4326")  
# Recortar o dataset usando o buffer
clipped_ds = data_rio['pr'].rio.clip(buffer.geometry, buffer.crs)
# Salvar o resultado ou fazer mais operações
clipped_ds.to_netcdf("saida_clipped.nc")
recortado= xr.open_dataset(f'{saida}/saida_clipped.nc')
#####################################################################################################################################

# plotagem #
fig, ax = plt.subplots(figsize=(10, 6), subplot_kw={'projection': ccrs.PlateCarree()})
ax.set_xlim([shp.bounds.minx.min(), shp.bounds.maxx.max()])
ax.set_ylim([shp.bounds.miny.min(), shp.bounds.maxy.max()])

recortado['pr'].isel(time=0).plot(ax=ax, transform=ccrs.PlateCarree(), cbar_kwargs={'label':'precipitação (mm/dia'})
#buffer.plot(ax=ax, transform=ccrs.PlateCarree(), alpha=0.5, facecolor='blue')

shp_ma.plot(ax=ax, facecolor='none', linewidth=3) 
shp_pa.plot(ax=ax, facecolor='none', linewidth=3)
shp.plot(ax=ax)
plt.axis('equal')




plt.show()
quit()












#data['pr'].isel(time=0).plot(ax=ax, transform=ccrs.PlateCarree(), cbar_kwargs={'label':'precipitação (mm/dia'})
#colorbar = fig.colorbar(pr, ax=ax, orientation='vertical')
#colorbar.set_label('Precipitação (mm/dia)') 
