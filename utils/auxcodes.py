import numpy as np
from scipy.optimize import leastsq
import scipy
import os,sys
from pipeline_logging import run_log
from subprocess import call
from astropy.io import fits
from astropy.wcs import WCS
import signal
from facet_offsets import RegPoly
import pyregion
from surveys_db import use_database,update_status

# these are small routines used by more than one part of the pipeline

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def separator(s):
    print
    print "%s============================== %s =============================%s"%(bcolors.FAIL,s,bcolors.ENDC)
    print

    
def die(s,database=True):
    print bcolors.FAIL+s+bcolors.ENDC
    if database and use_database():
        update_status(None,'Failed')
    raise Exception(s)

def report(s):
    print bcolors.OKGREEN+s+bcolors.ENDC

def warn(s):
    print bcolors.OKBLUE+s+bcolors.ENDC

def run(s,proceed=False,dryrun=False,log=None,quiet=False):
    report('Running: '+s)
    if not dryrun:
#      retval=os.system(s)
        if log is None:
            retval=call(s,shell=True)
        else:
            retval=run_log(s,log,quiet)
        if not(proceed) and retval!=0:
           os.system('CleanSHM.py')
           die('FAILED to run '+s+': return value is '+str(retval))
        return retval
    else:
        warn('Dry run, skipping this step')

def get_rms(hdu,boxsize=1000,niter=20,eps=1e-6,verbose=False):

    data=hdu[0].data
    if len(data.shape)==4:
        _,_,ys,xs=data.shape
        subim=data[0,0,ys/2-boxsize/2:ys/2+boxsize/2,xs/2-boxsize/2:xs/2+boxsize/2].flatten()
    else:
        ys,xs=data.shape
        subim=data[ys/2-boxsize/2:ys/2+boxsize/2,xs/2-boxsize/2:xs/2+boxsize/2].flatten()
    subim=subim[~np.isnan(subim)]
    oldrms=1
    for i in range(niter):
        rms=np.std(subim)
        if verbose: print len(subim),rms
        if np.abs(oldrms-rms)/rms < eps:
            return rms
        subim=subim[np.abs(subim)<5*rms]
        oldrms=rms
    raise Exception('Failed to converge')

def flatten(f):
    """ Flatten a fits file so that it becomes a 2D image. Return new header and data """

    naxis=f[0].header['NAXIS']
    if naxis<2:
        raise RadioError('Can\'t make map from this')
    if naxis==2:
        return fits.PrimaryHDU(header=f[0].header,data=f[0].data)

    w = WCS(f[0].header)
    wn=WCS(naxis=2)
    
    wn.wcs.crpix[0]=w.wcs.crpix[0]
    wn.wcs.crpix[1]=w.wcs.crpix[1]
    wn.wcs.cdelt=w.wcs.cdelt[0:2]
    wn.wcs.crval=w.wcs.crval[0:2]
    wn.wcs.ctype[0]=w.wcs.ctype[0]
    wn.wcs.ctype[1]=w.wcs.ctype[1]
    
    header = wn.to_header()
    header["NAXIS"]=2
    copy=('EQUINOX','EPOCH','BMAJ', 'BMIN', 'BPA', 'RESTFRQ', 'TELESCOP', 'OBSERVER')
    for k in copy:
        r=f[0].header.get(k)
        if r is not None:
            header[k]=r

    slice=[]
    for i in range(naxis,0,-1):
        if i<=2:
            slice.append(np.s_[:],)
        else:
            slice.append(0)
        
    hdu = fits.PrimaryHDU(header=header,data=f[0].data[slice])
    return hdu

class Catcher():
    def __init__(self):
        self.stop=False
        signal.signal(signal.SIGUSR1, self.handler)
        signal.siginterrupt(signal.SIGUSR1,False) 
    def handler(self,signum,frame):
        print 'Signal handler called with signal', signum
        self.stop=True
    def check(self):
        if self.stop:
            if use_database():
                update_status(None,'Stopped')
            os.system('CleanSHM.py')
            raise RuntimeError('Caught user-defined exception, terminating gracefully')

def find_imagenoise(workingimage,estnoise):
    f = fits.open(workingimage)
    noisearray = f[0].data.flatten()
    maxpixel = np.max(noisearray)
    noisearray = np.random.permutation(noisearray)[:10000]
    #noisepix = np.array(filter(lambda x: abs(x) > 10E-8,noisearray)) # Filter out the edge pixels which have values around 1E-10
    noisepix = np.array(filter(lambda x: abs(x)<50.0*estnoise,noisepix))
    f.close()
    rms = fit_gaussian_histogram(noisepix,False)
    return rms

#------------------------------------------------------------

def model_gaussian(t, coeffs):
    return coeffs[0] + coeffs[1] * np.exp( - ((t-coeffs[2])**2.0/(2*coeffs[3]**2)))

#------------------------------------------------------------
def residuals_gaussian(coeffs, y, t):
    return y - model_gaussian(t, coeffs)

#------------------------------------------------------------
def fit_gaussian_histogram(pixelvals,plotting):

    fitnumbers,cellsizes = np.histogram(pixelvals,100)
    sigmaguess = np.std(pixelvals)/(abs(cellsizes[1]-cellsizes[0]))
    x0 = [0.0,max(fitnumbers),np.where(fitnumbers==max(fitnumbers))[0][0],sigmaguess] #Offset amp, amp, x-offset, sigma
    t = np.arange(len(fitnumbers))
    x, flag = scipy.optimize.leastsq(residuals_gaussian, x0, args=(fitnumbers, t))

    if plotting:
        import matplotlib.pyplot as plt
        
        plt.plot(fitnumbers)
        plt.plot(t,fitnumbers,t,model_gaussian(t,x))
        plt.show()
        plt.close()
        plt.cla()

    #print 'Sigma is %s'%(x[3]*abs(cellsizes[1]-cellsizes[0]))
    
    return (x[3]*abs(cellsizes[1]-cellsizes[0]))

def get_rms_array(subim,size=500000,niter=25,eps=1e-6,verbose=False):
    oldrms=1
    if len(subim)>size:
        subim=np.random.choice(subim,size,replace=False)
    for i in range(niter):
        rms=np.std(subim)
        if verbose: print len(subim),rms
        if np.abs(oldrms-rms)/rms < eps:
            return rms
        subim=subim[np.abs(subim)<5*rms]
        oldrms=rms
    print 'Warning -- failed to converge!',rms,oldrms
    return rms

def polylist_to_string(poly):
    polystring='polygon('
    for j in range(0,len(poly)):
        polystring += ('%s,%s,'%(poly[j][0],poly[j][1]))
    polystring = polystring[:-1]+')'
    return polystring

def convert_regionfile_to_poly(inregfile):
    cra,cdec=get_centpos()
    r=RegPoly(inregfile,cra,cdec)
    polystringlist = []
    for p in sorted(list(set(r.plab_int))):
        polylist = []
        for i,poly in enumerate(r.oclist):
            if r.plab_int[i]==p:
                polylist.append(poly)
        # now polylist contains all the polygons in this direction, in order
        polystring = 'fk5;'
        for poly in polylist:
            polystring += polylist_to_string(poly)+';'
        polystringlist.append(polystring[:-1])
    return polystringlist

def get_rms_map(infilename,ds9region,outfilename):

    polylist = convert_regionfile_to_poly(ds9region)
    hdu=fits.open(infilename)
    hduflat = flatten(hdu)
    map=hdu[0].data

    for direction,ds9region in enumerate(polylist):
        print direction,ds9region
        r = pyregion.parse(ds9region)
        manualmask = r.get_mask(hdu=hduflat)
        rmsval = get_rms_array(hdu[0].data[0][0][np.where(manualmask == True)])
        hdu[0].data[0][0][np.where(manualmask == True)] = rmsval
        print 'RMS = %s for direction %i'%(rmsval,direction)
    hdu.writeto(outfilename,clobber=True)

def get_rms_map2(infilename,ds9region,outfilename):

    runcommand = "MakeMask.py --RestoredIm=%s --OutName=rmsmapmask --Th=%s --Box=50,2 --OutNameNoiseMap=%s.noise"%(infilename,3.0,infilename)

    run(runcommand,log=None)

    infilename = '%s.noise.fits'%infilename
    polylist = convert_regionfile_to_poly(ds9region)
    hdu=fits.open(infilename)
    hduflat = flatten(hdu)
    map=hdu[0].data

    for direction,ds9region in enumerate(polylist):
        print direction,ds9region
        r = pyregion.parse(ds9region)
        manualmask = r.get_mask(hdu=hduflat)
        rmsval = np.mean(hdu[0].data[0][0][np.where(manualmask == True)])
        hdu[0].data[0][0][np.where(manualmask == True)] = rmsval
        print 'RMS = %s for direction %i'%(rmsval,direction)
    hdu.writeto(outfilename,clobber=True)
    
class dotdict(dict):
    """dot.notation access to dictionary attributes. Quick hack to allow us to pass options in the form that smoothsols expects"""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__

def sepn(r1,d1,r2,d2):
    """
    Calculate the separation between 2 sources, RA and Dec must be
    given in radians. Returns the separation in radians
    """
    # NB slalib sla_dsep does this
    # www.starlink.rl.ac.uk/star/docs/sun67.htx/node72.html
    cos_sepn=np.sin(d1)*np.sin(d2) + np.cos(d1)*np.cos(d2)*np.cos(r1-r2)
    sepn = np.arccos(cos_sepn)
    # Catch when r1==r2 and d1==d2 and convert to 0
    sepn = np.nan_to_num(sepn)
    return sepn

def getpos(ms):
    t = pt.table(ms+ '/OBSERVATION', readonly=True, ack=False)
    name=t[0]['LOFAR_TARGET']

    t = pt.table(ms+'/FIELD', readonly=True, ack=False)

    direction = t[0]['PHASE_DIR']
    ra, dec = direction[0]

    if (ra<0):
        ra+=2*np.pi;

    return name[0],ra*(180/np.pi),dec*(180/np.pi)

def getposim(image):
    import pyrap.tables as pt
    hdus=fits.open(image)
    ra=hdus[0].header['CRVAL1']
    dec=hdus[0].header['CRVAL2']
    return ra,dec

def get_centpos():
    checklist=['image_dirin_SSD_init.dirty.fits','image_full_ampphase_di_m.NS_shift.app.facetRestored.fits','image_full_ampphase_di.dirty.fits']
    for f in checklist:
        if os.path.isfile(f):
            return getposim(f)
    raise RuntimeError('Cannot find image with central RA, DEC in working directory')

class MSList(object):
    """
    Class to look at all the MSs in an MS list and store some basic
    information about them in a data structure
    """
    def __init__(self,mslist,mss=None):
        """
        mslist is the MS list filename
        """
        import pyrap.tables as pt
        if mss is not None:
            self.mss=mss
            self.mslist=None
        else:
            self.mslist=mslist
            self.mss=[s.strip() for s in open(mslist).readlines()]
        self.obsids = [os.path.basename(ms).split('_')[0] for ms in self.mss]
        self.freqs=[]
        self.channels=[]
        self.hascorrected=[]
        self.dysco=[]
        for ms in self.mss:
            t = pt.table(ms,readonly=True,ack=False)
            colname='CORRECTED_DATA'
            try:
                dummy=t.getcoldesc(colname)
            except RuntimeError:
                dummy=None
            self.hascorrected.append(not(dummy is None))
            self.dysco.append('Dysco' in t.showstructure())
            t.close()
            t = pt.table(ms+'/SPECTRAL_WINDOW', readonly=True, ack=False)
            self.freqs.append(t[0]['REF_FREQUENCY'])
            self.channels.append(t[0]['CHAN_FREQ'])
