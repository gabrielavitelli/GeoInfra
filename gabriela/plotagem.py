# -*- coding: utf-8 -*-
"""
Created on Mon May 20 10:12:11 2024

@author: Usuario
"""

import xarray as xr
import matplotlib.pyplot as plt
import os
import numpy as np

ds = xr.open_dataset('/mnt/c/Users/Usuario/gabriela/regcm4_1980_2005_media_mensal_05.nc')
print (ds['pr'].values)
print ('\n\n')

ds = xr.open_dataset('/mnt/c/Users/Usuario/gabriela/media_precipitacao_regcm4_1980_2005/regcm4_1980_2005_media_mensal_05.nc')
print (ds['pr'].values)

quit()



# Diretório onde os arquivos NetCDF estão armazenados
data_dir = '/mnt/c/Users/Usuario/gabriela/'

# Lista de arquivos NetCDF
files = [f'regcm4_1980_2005_media_mensal_{mes:02d}.nc' for mes in range(1, 13)]

# Caminhos completos dos arquivos
file_paths = [os.path.join(data_dir, f) for f in files]

def plot_monthly_means(file_paths, variable='pr'):
    monthly_means = []

    for file_path in file_paths:
        ds = xr.open_dataset(file_path)
        print (ds['pr'].values)

        data = ds[variable]

        # Calcula a média espacial (média sobre latitude e longitude)
        spatial_mean = data.mean(dim=['y', 'x']).values
        monthly_means.append(spatial_mean)
        print (monthly_means)
        quit()
        
    # Plotando os dados
    months = list(range(1, 13))
    plt.figure(figsize=(10, 6))
    plt.plot(months, monthly_means, marker='o')
    plt.xticks(months, ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])
    plt.xlabel('Month')
    plt.ylabel('Precipitação acumulada média (mm/dia)')
    plt.title('Média Mensal de Precipitação Acumulada (1980-2005)')
    plt.grid(True)
    plt.show()

# Chamar a função para plotar os dados
#plot_monthly_means(file_paths, variable='pr')

def remover_outliers(data_array):
    q_high = data_array.quantile(0.9)
    return data_array.where((data_array <= q_high), np.nan)


for file_path in file_paths:
    # Abrir o dataset
    ds = xr.open_dataset(file_path)
    ds['pr'] = remover_outliers(ds['pr'])
    # Verificar as variáveis no dataset
    #print(ds)
    
    # Selecionar a variável de precipitação (substitua 'precipitation' pelo nome correto da variável se for diferente)
    precipitation = ds['pr'].values

    print (precipitation)

    
    # Calcular a média espacial (sobre latitude e longitude)
    #spatial_mean = ds['pr'].mean(dim=['y', 'x']).values
    
    # Verificar os valores da média mensal
    #print(f'Média mensal de precipitação acumulada: {spatial_mean} mm/mês')

    # Verificar a estatística básica
    min_value = np.nanmin(precipitation)
    max_value = np.nanmax(precipitation)

    print(f'Mínima da precipitação: {min_value} mm/mês')

    print(f'Máxima da precipitação: {max_value} mm/mês')
    
    print ('\n\n')
    # Verificar a distribuição dos dados
    #hist = np.histogram(spatial_mean, bins=10)
   # print(f'Histograma da precipitação: {hist}')
