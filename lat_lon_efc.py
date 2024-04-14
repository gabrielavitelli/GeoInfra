# dados diários de precipitacao mm/dia CHIRPS para janeiro/fev de 2024
# precisa de uma parte do script que baixe os dados # 

import pandas as pd
import os
from glob import glob
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
import matplotlib.dates as mdates
import geopandas as gpd

#entrada="C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/dados/teste.nc4"
entrada="C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/dados/chirps-v2.0.2024.days_p05.nc"

ds = xr.open_mfdataset(entrada)
print (ds.time )

gdf = gpd.read_file('C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/extensao1_efc.shp')
gdf2 = gpd.read_file('C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/efc_extensao2.shp')
#print(gdf.head())
print(gdf2.head())

# Combinar as geometrias das duas GeoDataFrames
combined_gdf = gpd.GeoDataFrame(geometry=[gdf.unary_union, gdf2.unary_union])

# Criar uma única geometria combinada
final_geometry = combined_gdf.unary_union

# Você também pode criar um novo GeoDataFrame que inclui essa geometria combinada
final_gdf = gpd.GeoDataFrame(geometry=[final_geometry], crs=gdf.crs)
# Calcular a extensão da geometria combinada
extent = final_geometry.bounds

print("Extensão da Geometria Combinada:")
print(f"Min Longitude: {extent[0]}")
print(f"Min Latitude: {extent[1]}")
print(f"Max Longitude: {extent[2]}")
print(f"Max Latitude: {extent[3]}")