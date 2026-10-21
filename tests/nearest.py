import os
os.environ["DISABLE_BACKUP_SCHEDULER"]="1"
from app import create_app
from app import airports as ap
app=create_app()
with app.app_context():
    for name,(lat,lon) in {"Sheridan (KD5VCF)":(34.315231,-92.410357),"Little Rock":(34.75,-92.29),"Fayetteville":(36.06,-94.17)}.items():
        r=ap.nearest(lat,lon,3)
        print(name,"->",[(x["ident"],round(x["miles"],1)) for x in r])
    r=ap.nearest(34.315231,-92.410357,3,("large_airport","medium_airport","small_airport"))
    print("incl. small:",[(x["ident"],x["name"][:22],round(x["miles"],1)) for x in r])
