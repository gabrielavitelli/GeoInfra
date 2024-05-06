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
#####################################################################################################################################
#/mnt/c/Users/Usuario/gabriela/
#entrada='/mnt/c/Users/Usuario/gabriela//dados/CORDEX/pr_SAM-20_MIROC-MIROC5_rcp85_r1i1p1_INPE-Eta_v1_day_20960101-20991231.nc'
entrada='/mnt/c/Users/Usuario/gabriela/dados/CORDEX/historico/dataset-projections-cordex-domains-single-levels-3896b270-7908-453f-8171-ddedafe35bde/pr_SAM-20_MIROC-MIROC5_historical_r1i1p1_INPE-Eta_v1_day_20010101-20051231.nc'

dado_bruto = xr.open_dataset(entrada)

#.sel(lat=latitude, lon=longitude, time=dado_bruto.time[0:366], method='nearest')
# Calculando a resolução para latitude e longitude
lat_res = abs(dado_bruto['lat'][1] - dado_bruto['lat'][0])
lon_res = abs(dado_bruto['lon'][1] - dado_bruto['lon'][0])

print(f"Resolução da Latitude: {lat_res} graus")
print(f"Resolução da Longitude: {lon_res} graus")


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
'''cdo = Cdo()
print(cdo.version())
tempPath = './tmp/'
cdo = Cdo(tempdir=tempPath)
cdo.cleanTempDir()
#print (cdo.operators)
cdo.debug=True

#####################################################################################################################################
# operacoes com cdo, conversao de unidade 
cdo.copy(input=entrada, options='-b F64')
cdo.mulc(86400, input=entrada, output='mmday.nc') #muda de kg/m2/s para mm/dia
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
ax.set_xlabel('Longitude')
ax.set_ylabel('Latitude')

#ax.set_xticks(np.arange(shp_pa[0], sho_pa[-1], step=1)) # -120, 1
#ax.set_yticks(np.arange(shp_pa[0], shp_pa[-1], step=1))

ax.set_xticks(np.arange(recortado['pr'].lon[0], recortado['pr'].lon[-1], step=1)) # -120, 1
ax.set_yticks(np.arange(recortado['pr'].lat[0], recortado['pr'].lat[-1], step=1))
		
plt.axis('equal')




plt.show()
'''

############################################################################################################################################
# recorte para ponto 1:               latitude -4.41                      longitude: -46.75                altitude: 192

# recortando dados
latitude = -4.41
longitude = -46.75

#print ('pr mm/dia')
#print ((dado_bruto['pr']*86400).sel(time=dado_bruto.time[0:7]).values)

###############################################################################################################
dado_sel_bruto=dado_bruto.sel(lat=latitude, lon=longitude, time=dado_bruto.time[0:7], method='nearest')
pr1 = dado_sel_bruto['pr']
df1 = pr1.to_dataframe()
try:
    df1.to_csv('dadobrutoNaoTransformado.csv', columns=['lat', 'lon', 'pr'], sep=';', decimal = ",")
except:
    print ('Ja existe arquivo dado bruto')
###############################################################################################################


dado_sel_2001_2005=dado_bruto.sel(lat=latitude, lon=longitude, method='nearest')
dado_sel=dado_bruto.sel(lat=latitude, lon=longitude, time=dado_bruto.time[0:366], method='nearest')
pr = dado_sel['pr']*86400
pr_2001_2005 = dado_sel_2001_2005['pr']*86400
df =pr_2001_2005.to_dataframe()
try:
    df.to_csv('FazendaPedreiras_2001_2005.csv', columns=['lat', 'lon', 'pr'], sep=';', decimal = ",")

except:
    print ('ja existe um arquivo FazendaPedreiras_2001.csv')
quit()
# configurando tempo e pegando precipitacao
tempo = dado_sel.time.data
tempo_configurado = pd.to_datetime(tempo)
dia_juliano = tempo_configurado.dayofyear

# plotando
plt.figure()
plt.plot(dia_juliano.astype(str).to_numpy(), pr.to_numpy())#, marker='o',linestyle='None')
plt.title("Série temporal de precipitação para 2001 para Fazenda Pedreiras, dados do CORDEX - MIROC-MIROC5")
plt.ylabel('Precipitação')

plt.xticks(np.arange(365, step=31), ['jan', 'fev', 'mar', 'abr', 'maio', 'jun', 'jul', 'agos', 'set', 'out', 'nov', 'dez'], rotation=20)
#plt.show() 
############################################################################################################################################
# recorte para ponto 2:               latitude -4.33                      longitude: -46.49                altitude: 67

# recortando dados
p2_latitude = -4.33
p2_longitude = -46.49
p2_dado_sel=dado_bruto.sel(lat=p2_latitude, lon=p2_longitude, time=dado_bruto.time[0:366], method='nearest')
p2_pr=p2_dado_sel['pr']*86400
p2_df =p2_pr.to_dataframe()
try:
    p2_df.to_csv('ponte_BR222.csv', columns=['lat', 'lon', 'pr'], sep=';', decimal = ",")
except:
    print ('ja existe um arquivo ponte_BR222_2001.csv')
# configurando tempo e pegando precipitacao
tempo = dado_sel.time.data
tempo_configurado = pd.to_datetime(tempo)
dia_juliano = tempo_configurado.dayofyear

# plotando
plt.figure()
plt.plot(dia_juliano.astype(str).to_numpy(), pr.to_numpy())#, marker='o',linestyle='None')
plt.title("Série temporal de precipitação para 2001 para Ponte BR-222, dados do CORDEX - MIROC-MIROC5")
plt.ylabel('Precipitação')

plt.xticks(np.arange(365, step=31), ['jan', 'fev', 'mar', 'abr', 'maio', 'jun', 'jul', 'agos', 'set', 'out', 'nov', 'dez'], rotation=20)
#plt.show() 

############################################################################################################################################
# recorte para ponto 3:               latitude                       longitude:                 altitude: 
#Prefeitura Municipal de Buriticupu
# recortando dados
p3_latitude = -4.32
p3_longitude = -46.46
p3_dado_sel=dado_bruto.sel(lat=p3_latitude, lon=p3_longitude, time=dado_bruto.time[0:366], method='nearest')
p3_pr=p3_dado_sel['pr']*86400
p3_df =p3_pr.to_dataframe()
try:
    p3_df.to_csv('PrefeituraMunicipalBuriciupu_2001.csv', columns=['lat', 'lon', 'pr'], sep=';', decimal = ",")
except:
    print ('ja existe um arquivo PrefeituraMunicipalBuriciupu_2001.cs')
#

# configurando tempo e pegando precipitacao
tempo = dado_sel.time.data
tempo_configurado = pd.to_datetime(tempo)
dia_juliano = tempo_configurado.dayofyear

# plotando
plt.figure()
plt.plot(dia_juliano.astype(str).to_numpy(), p3_pr.to_numpy())#, marker='o',linestyle='None')
plt.title("Série temporal de precipitação para 2001 para Prefeitura Municipal de Buriticupu, dados do CORDEX - MIROC-MIROC5")
plt.ylabel('Precipitação')

plt.xticks(np.arange(365, step=31), ['jan', 'fev', 'mar', 'abr', 'maio', 'jun', 'jul', 'agos', 'set', 'out', 'nov', 'dez'], rotation=20)
plt.show() 

############################################################################################################################################
############################################################################################################################################
# recorte para ponto 14:               latitude                       longitude:                 altitude: 
# Arotoí Grande

# recortando dados
p4_latitude = -3.77
p4_longitude = -45.22   
    
'''
# recortando dados
#latitude = -4.41
#longitude = -46.75
dado_sel=dado_bruto.sel(time=dado_bruto.time[0:365])
print (dado_sel)
df =dado_sel.to_dataframe()
pd.set_option('display.max_colwidth', 25)
#try:
   # df.to_csv('retangulo da extensao_efc.csv', sep=';')

# configurando tempo e pegando precipitacao
tempo = dado_sel.time.data
tempo_configurado = pd.to_datetime(tempo)
dia_juliano = tempo_configurado.dayofyear
pr=dado_sel['pr']*8640
print (pr.groupby(pr.time.dt.month).sum())
pr=pr.groupby(pr.time.dt.month).sum()
media_espacial =pr.mean(dim=['lat', 'lon'])

# plotando
plt.plot(pr.month.to_numpy(), media_espacial.to_numpy())#, marker='o',linestyle='None')
plt.title("Série temporal de precipitação acumulada para 2096 para toda extensao da EFC, dados do CORDEX - MIROC-MIROC5")
plt.ylabel('Precipitação')

plt.xticks(np.arange(12, step=1), ['jan', 'fev', 'mar', 'abr', 'maio', 'jun', 'jul', 'agos', 'set', 'out', 'nov', 'dez'], rotation=20)



plt.show()



#pd.DataFrame








print ('---------------------------------------')
print ('dado')
print (dado_sel)

print ('---------------------------------------')
print ('dias')
print (dia_juliano)


print ('---------------------------------------')
print ('dados pr')
print (pr)


#data['pr'].isel(time=0).plot(ax=ax, transform=ccrs.PlateCarree(), cbar_kwargs={'label':'precipitação (mm/dia'})
#colorbar = fig.colorbar(pr, ax=ax, orientation='vertical')
#colorbar.set_label('Precipitação (mm/dia)') 
'''