import numpy as np
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import xarray as xr
import rioxarray
import geopandas as gpd
from shapely.geometry import box
#####################################################################################################################################

# shapefiles #
caminho_geoinfra = "/mnt/c/Users/Usuario/gabriela/dados/shp/"
shp_caminho_pa = f'{caminho_geoinfra}PA_Municipios_2022/PA_Municipios_2022.shp'
shp_pa= gpd.read_file(shp_caminho_pa)
shp_caminho_ma = f'{caminho_geoinfra}MA_Municipios_2022/MA_Municipios_2022.shp'
shp_ma=gpd.read_file(shp_caminho_ma)
# efc #
shp= gpd.read_file("/mnt/c/Users/Usuario/gabriela/dados/shp/EFC/extensao_efc_shp.shp")

#####################################################################################################################################
# no meu github tem o lat_lon_efc.py que pega as duas camadas de linha kml que transformei em shp e 
# usa .bounds do geopandas para extrair coordenadas

Min_Longitude = 309.87482580010232 #-50.12517419989768
Min_Latitude = -6.014153889871978
Max_Longitude = 315.70681579034048 #-44.29318420965952
Max_Latitude = -2.621573681489544
#####################################################################################################################################

ds = xr.open_dataset('/mnt/c/Users/Usuario/gabriela/dados/resolucao/cmip6_latlon.nc', engine='netcdf4')
dado_sel=ds.sel(lat=slice(Min_Latitude, Max_Latitude), lon=slice(Min_Longitude, Max_Longitude), time=ds.time[0])
#####################################################################################################################################
# mascara #

data = dado_sel.rio.set_spatial_dims(x_dim="lon", y_dim="lat", inplace=True)

# Renomear dimensões para x e y
data = dado_sel.rename({'lat': 'x', 'lon': 'y'})

# Definir dimensões espaciais
data = data.rio.set_spatial_dims(x_dim='x', y_dim='y')

#  buffer em torno da linha 
shp = shp.set_crs('EPSG:4326')
buffer = shp.to_crs(epsg=4326).buffer(0.5)
buffer = buffer.translate(xoff=360)  # Ajustar de -180 a 180 para para 0 a 360


# Converter o xarray dataset para um rioxarray para facilitar a operação de corte
data_rio = data.rio.write_crs("EPSG:4326")  
data_rio['pr'] = data_rio['pr']*86400
pr=data_rio['pr']
print("Limites dos dados:", data_rio['pr'].rio.bounds())

#buffer = shp.buffer(0.5)
data_bounds = data_rio['pr'].rio.bounds()

# Ajustar os limites do buffer
buffer_minx, buffer_miny, buffer_maxx, buffer_maxy = buffer.total_bounds
data_minx, data_miny, data_maxx, data_maxy = data_bounds

# Ajustar os limites do buffer para garantir que estejam dentro dos limites dos dados
adjusted_buffer_minx = min(buffer_minx, data_minx)
adjusted_buffer_miny = min(buffer_miny, data_miny)
adjusted_buffer_maxx = max(buffer_maxx, data_maxx)
adjusted_buffer_maxy = max(buffer_maxy, data_maxy)

# Ajustar a geometria do buffer com os novos limites
adjusted_buffer = gpd.GeoSeries([box(adjusted_buffer_minx, adjusted_buffer_miny, adjusted_buffer_maxx, adjusted_buffer_maxy)], crs="EPSG:4326")

# Verificar os limites do buffer
print("Limites do buffer:", buffer.bounds)


print(data_rio.rio.crs)
print(buffer.crs)
#quit()
# Recortar o dataset usando o buffer
clipped_ds = data_rio['pr'].rio.clip(adjusted_buffer.geometry, adjusted_buffer.crs)


print (clipped_ds)

# Salvar o resultado ou fazer mais operações
clipped_ds.to_netcdf("saida_clipped.nc")
recortado= xr.open_dataset(f'saida_clipped.nc')

#####################################################################################################################################

# plotagem #
fig, ax = plt.subplots(figsize=(10, 6), subplot_kw={'projection': ccrs.PlateCarree()})
ax.set_xlim([shp.bounds.minx.min(), shp.bounds.maxx.max()])
ax.set_ylim([shp.bounds.miny.min(), shp.bounds.maxy.max()])

pr.plot(ax=ax, transform=ccrs.PlateCarree(), cbar_kwargs={'label':'precipitação (mm/dia'})
#buffer.plot(ax=ax, transform=ccrs.PlateCarree(), alpha=0.5, facecolor='blue')

shp_ma.plot(ax=ax, facecolor='none', linewidth=3) 
shp_pa.plot(ax=ax, facecolor='none', linewidth=3)
shp.plot(ax=ax)
ax.set_xlabel('Longitude')
ax.set_ylabel('Latitude')


bounds = shp.total_bounds
minx, miny, maxx, maxy = bounds

# Definir os ticks do eixo x (longitude)
ax.set_xticks(np.arange(minx, maxx, step=1))

# Definir os ticks do eixo y (latitude)
ax.set_yticks(np.arange(miny, maxy, step=1))

#ax.set_yticks(np.arange(recortado['pr'].x[0], recortado['pr'].x[-1], step=1)) # -120, 1
#ax.set_xticks(np.arange(recortado['pr'].y[0], recortado['pr'].y[-1], step=1))


plt.title(f"precipitacao para 1950-01-01 - MIROC6")
plt.axis('equal')




plt.show()
