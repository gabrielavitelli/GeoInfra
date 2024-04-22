from cdo import *
import xarray as xr
import matplotlib.pyplot as plt
import numpy as np

entrada='/mnt/c/Users/Usuario/gabriela/dados/CORDEX/pr_SAM-20_MIROC-MIROC5_rcp85_r1i1p1_INPE-Eta_v1_day_20960101-20991231.nc'
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

########### ate aqui dados de janeiro no recorte da EFC para precipitacao #######

cdo.fldmean(input=f'{saida}/area.nc', returnXArray='pr', output=f'{saida}/saida.nc').plot()

plt.ylabel('Precipitacao (mm/dia)')
plt.show()



quit()
