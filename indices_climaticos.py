# dados diários de precipitacao mm/dia CHIRPS para janeiro/fev de 2024
# precisa de uma parte do script que baixe os dados para ano de escolha# 

import pandas as pd
import os
from glob import glob
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
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
# carrega o dado e ajusta as coordenadas

# CMIP6 
# 1989-01-01 12:00:00 ... 1999-12-30 12:00:00

entrada = "C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/dados/CMIP6/pr_day_HadGEM3-GC31-MM_historical_r1i1p1f3_gn_19890101-19991230_v20191207.nc"
#entrada = "C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/dados/teste.nc4"
ds = xr.open_mfdataset(entrada)
ds_rec=converte_coordenada(ds)

#shp
shp_caminho_pa = "C:/Users/gabri/OneDrive/ESTUDOS/DADOS/PA_Municipios_2022/PA_Municipios_2022.shp"
shp_pa=gpd.read_file(shp_caminho_pa)
shp_caminho_ma = "C:/Users/gabri/OneDrive/ESTUDOS/DADOS/MA_Municipios_2022/MA_Municipios_2022.shp"
shp_ma=gpd.read_file(shp_caminho_ma)

####################################################################################################
# gera um mapa para conferir localizacao 

# pega coordenadas do dado
min_latitude, max_latitude = ds_rec.lat.min().values, ds_rec.lat.max().values
min_longitude, max_longitude = ds_rec.lon.min().values, ds_rec.lon.max().values

# retangulo para a area do dado
area = box(min_longitude, min_latitude, max_longitude, max_latitude)
area_dataframe = gpd.GeoDataFrame({'geometry': [area]}, crs=shp_ma.crs)

# plotagem
fig, ax = plt.subplots()
shp_ma.plot(ax=ax, color='blue')  
shp_pa.plot(ax=ax, color='blue')  
area_dataframe.plot(ax=ax, color='red', alpha=0.5)  # Plotar retângulo com transparência
plt.title ("Localização")
#plt.show()
####################################################################################################
# gera um outro tipo de mapa para localizacao
'''
m = folium.Map(location=[(Min_Latitude + Max_Latitude) / 2, (Min_Longitude + Max_Longitude) / 2], zoom_start=6)

# Desenhar um retângulo semi-transparente
folium.Rectangle(
    bounds=[[Min_Latitude, Min_Longitude], [Max_Latitude, Max_Longitude]],
    color='#0078A8',  # Cor da borda
    fill=True,
    fill_color='#0078A8',  # Cor de preenchimento
    fill_opacity=0.5  # Transparência do preenchimento
).add_to(m)

#m.save("MIRO6.html")
'''
####################################################################################################

# no caso os dados estavam em kg_m2_s e quero transformar em mm/dia
precip = ds_rec['pr'] * 86400
print (precip)
maio=precip.sel(time=slice("1989-05-01", "1989-05-30"))
print (maio)

fig, ax = plt.subplots(figsize=(10, 6))
media_maio= maio.mean(dim=['lat','lon'])

ax.plot(media_maio, marker='o', linestyle='-')

#ax.plot(precip['time'].values, precip[:,0,0].values, marker='o', linestyle='-')
ax.set_title(f'Maio')
ax.set_xlabel('tempo')
ax.set_ylabel('chuva (mm/dia)')
ax.grid(True)

plt.tight_layout(rect=[0, 0.03, 1, 0.95])  # Ajustar o layout
plt.show()



#print (precip)
###############################################################################################
# CDD #
# numero maximo de dias secos consecutivos no periodo analisado (chuva <1mm)

def calculate_cdd(data):       
    resultados = {}
    # itera no tempo, para todas lat lon
    for ano, indice in data.groupby('time.year').groups.items():
        dado_ano = data.isel(time=indice)
        
        for meses, mindices in dado_ano.groupby('time.month').groups.items():    
            dado_mes = dado_ano.isel(time=mindices)
            # contadores
            dias_secos = 0
            max_dias_secos = 0

            # Verifica cada dia no mês
            for dia in range(len(dado_mes['time'].values)):
                precipitacao_dia = dado_mes.isel(time=dia).values
                #print("Valores de precipitação no dia", dia, ":", precipitacao_dia)
                
                # verifica se trata-se de uma estiagem
                if ((precipitacao_dia < 1).all()):
                    dias_secos+=1
                
                else: # choveu
                    if dias_secos > 0 and dias_secos > max_dias_secos:
                        # agora que acabou período de estiagem atualiza o intervalo max
                        max_dias_secos = dias_secos
                        dias_secos=0
        
                    if dias_secos > 0 and dias_secos > max_dias_secos:
                        max_dias_secos = dias_secos
                        
                    resultados[(ano, meses)] = max_dias_secos  
            print (meses)
            #print (precipitacao_dia.values)
            print ("-----------------------------------")
    return resultados

cdd_meses = calculate_cdd(precip)
df = pd.DataFrame([(key[0], key[1], value) for key, value in cdd_meses.items()], columns=['Ano', 'Mes', 'CDD'])
df.sort_values(by=['Ano', 'Mes'])

#df_maio= df.sel(dim='time'=='5')
#print (df_maio)

plt.tight_layout(rect=[0, 0.03, 1, 0.95])  # Ajustar o layout
plt.show()
quit()
df.to_csv('cdd_meses.txt', index=False, sep='\t')
df.to_csv('cdd_meses.csv', index=False, sep=';')

quit()
###############################################################################################
# CWD #
# numero maximo de dias consecutivos com chuva no periodo analisado (chuva>=1mm)

# contadores
dias_chuvosos = 0
max_dias_chuvosos = 0
intervalo_tempo = (None, None)
seq = None

# itera no tempo, para todas lat lon
for tempo in range(len(precip['time'])):
    # verifica se trata-se de uma estiagem
    if ((precip.isel(time=tempo) >= 1).all()):
        dias_chuvosos+=1
        #print ("choveu")
        if seq is None: # achou o início de uma seca
            seq=precip['time'][tempo].values
        
    else: 
        if dias_chuvosos > 0 and dias_chuvosos > max_dias_chuvosos:
            # agora que acabou período de estiagem atualiza o intervalo max
            max_dias_chuvosos = dias_chuvosos
            intervalo_tempo=(seq, precip['time'][tempo-1].values)
            
        dias_chuvosos=0
        seq=None
if dias_chuvosos > 0 and dias_chuvosos > max_dias_chuvosos:
        max_dias_chuvosos = dias_chuvosos
        intervalo_tempo = (seq, precip['time'][-1].values)
    
print (f"CWD: {max_dias_chuvosos}")

print(f"Período de chuva de {intervalo_tempo[0]} até {intervalo_tempo[1]}")


quit()