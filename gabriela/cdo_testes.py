import os
from cdo import *


os.environ['CDO'] = 'C:/Users/Usuario/anaconda3/envs/cdo'

cdo = Cdo()


print (cdo.__version__())


