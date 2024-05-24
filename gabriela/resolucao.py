import xarray as xr
from glob import glob
import os

#########################################################################################################################################################
caminho_regcm4 = '/mnt/c/Users/Usuario/gabriela/dados/1981-2001/regcm4/pr*'

input_dir = '/mnt/c/Users/Usuario/gabriela/dados/1981-2001/regcm4/'
output_dir = '/mnt/e/GeoInfra/dados/regCM4/'
target_grid = 'target_file.txt'

#########################################################################################################################################################
def regrid_regcm4 (input_dir, output_dir, target_grid):
    
    arquivos = sorted(glob(os.path.join(caminho_regcm4)))
    for arquivo in arquivos:
        # Nome do arquivo de saída
        arquivo_nome = os.path.basename(arquivo)
        output_file = os.path.join(output_dir, f'regridded_{arquivo_nome}.gz')
        # Especificação do target grid
        '''target_grid = """
        gridtype = lonlat
        xsize    = 449
        ysize    = 353
        xfirst   = -118.2255
        xinc     = 0.05
        yfirst   = -62.25327
        yinc     = 0.05
        """'''

        # Comando CDO para regridar
        cdo_command = f"cdo remapbil,{target_grid} {arquivo} {output_file}"
        
        # Executar o comando
        os.system(cdo_command)
        print(f'Reescalado e salvo: {output_file}')

#########################################################################################################################################################

# Reescalar todos os arquivos RegCM4
regrid_regcm4(input_dir, output_dir, target_grid)
 
#########################################################################################################################################################
quit()