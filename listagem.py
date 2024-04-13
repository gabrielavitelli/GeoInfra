#Python 3.7.16

import sys
from pathlib import Path
import os
import shutil
from datetime import datetime 
import xarray as xr
import cartopy, cartopy.crs as ccrs  
import cartopy.io.shapereader as shpreader
import cartopy.feature as cfeature
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.axes

atual= data = datetime.now() 
data=data.strftime("%Y%m%d")

DIR_SCRIPT="/home/gabriela/treinamento/python/scripts"
DIR_DATA=DIR_SCRIPT+"/fig_dados/"+data
DIR_FIGS=DIR_SCRIPT+"/fig_dados"+"/www/"+data
DIR=DIR_SCRIPT+"/fig_dados/"
date_hour=DIR_DATA+"06"

atual= data = datetime.now() 
atual= atual.strftime("%Y%m%d")
gfs=atual+'06'
frc_formatado='006'

gfss=['meanSea', 'hybrid', 'atmosphere', 'surface', 'planetaryBoundaryLayer', 'isobaricInPa' ,'isobaricInhPa' , 'heightAboveGround', 'depthBelowLandLayer', 'heightAboveSea', 'atmosphereSingleLayer', 'lowCloudLayer',  'middleCloudLayer', 'highCloudLayer',  'cloudCeiling', 'heightAboveGroundLayer', 'tropopause', 'maxWind', 'isothermZero', 'highestTroposphericFreezing', 'pressureFromGroundLayer', 'sigmaLayer', 'sigma', 'potentialVorticity']

for var in gfss:
	ds = xr.open_dataset(f"{date_hour}/gfs.{gfs}.1p00.f{frc_formatado}", engine='cfgrib', filter_by_keys={'stepType': 'instant', 'typeOfLevel': var}) # 'hybrid'}) #isobaricInhPa
	print (ds.variables)

quit()