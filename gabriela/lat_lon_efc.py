import geopandas as gpd

# Ler os arquivos shapefile
#gdf = gpd.read_file('C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/extensao1_efc.shp')
#gdf2 = gpd.read_file('C:/Users/gabri/OneDrive/ESTUDOS/GeoInfra/efc_extensao2.shp')
gdf= gpd.read_file('C:/Users/Usuario/gabriela/dados/shp/EFC/efc_extensao2.shp')
gdf2 = gpd.read_file('C:/Users/Usuario/gabriela/dados/shp/EFC/extensao1_efc.shp')

# Combinar as geometrias das duas GeoDataFrames
combined_gdf = gpd.GeoDataFrame(geometry=[gdf.unary_union, gdf2.unary_union])

# Criar uma única geometria combinada
final_geometry = combined_gdf.unary_union

# Criar um novo GeoDataFrame que inclui essa geometria combinada
final_gdf = gpd.GeoDataFrame(geometry=[final_geometry], crs=gdf.crs)

coords_list = []


# Iterar sobre cada LineString no MultiLineString
for linestring in final_geometry.geoms:
    # Extender a lista de coordenadas com as coordenadas de cada LineString
    coords_list.extend(list(linestring.coords))

print("Coordenadas da Geometria Combinada:")
for coord in coords_list:
    print(coord)

