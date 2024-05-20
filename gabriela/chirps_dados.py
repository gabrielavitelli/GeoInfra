
import xarray as xr


# CHIRPS
entrada_chirps = "C:/Users/Usuario/gabriela/dados/chirps-v2.0.2024.days_p05.nc"
ds_chirps = xr.open_mfdataset(entrada_chirps)
print (ds_chirps)
quit()
