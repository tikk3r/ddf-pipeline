#!/usr/bin/env python
# Run pipeline download/unpack steps followed by the main job

from auxcodes import report,warn,die
from surveys_db import use_database,update_status
from download import download_dataset
from download_field import download_field
from unpack import unpack,unpack_db_update
from make_mslists import make_list,list_db_update
import sys
import os

rootdir='/beegfs/car/mjh'
os.chdir(rootdir)

name=sys.argv[1]

if name[0]!='P' and name[0]!='L':
    die('This code should be used only with field or observation names',database=False)

do_field=(name[0]=='P')

try:
    qsubfile=sys.argv[2]
except:
    qsubfile='/home/mjh/pipeline-master/ddf-pipeline/torque/pipeline.qsub'

try:
    os.mkdir(name)
except OSError:
    warn('Working directory already exists')

report('Downloading data')
if do_field:
    success=download_field(name)
else:
    success=download_dataset('https://lofar-webdav.grid.sara.nl','/SKSP/'+name+'/')

if not success:
    die('Download failed, see earlier errors',database=False)

os.chdir(rootdir+'/'+name)
    
report('Unpacking data')
unpack()
if do_field:
    unpack_db_update()
    
report('Deleting tar files')
os.system('rm *.tar.gz')

report('Making ms lists')
success=make_list()
if do_field:
    list_db_update(success)

if success:
    report('Submit job')
    os.system('qsub -N ddfp-'+name+' -v WD='+rootdir+'/'+name+' '+qsubfile)
    if do_field:
        update_status(name,'Queued')

else:
    die('make_list could not construct the MS list',database=False)
