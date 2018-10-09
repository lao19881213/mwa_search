#!/usr/bin/env python

from scipy.interpolate import griddata
import matplotlib
import matplotlib.pyplot as plt
from matplotlib import patches
import sys
import subprocess

# numerical and maths modules
import numpy as np
from astropy.coordinates import SkyCoord,EarthLocation,AltAz,angles
from astropy.time import Time
from astropy.io import fits
import astropy.units as u
from astropy.constants import c,k_B
from math import cos,sin,acos,asin

#utility and processing modules
import os,sys
#from mpi4py import MPI
import argparse
from mwapy import ephem_utils,metadata
from mwapy.pb import primary_beam as pb
from mwapy.pb.mwa_tile import h2e
#import urllib
#import urllib2
#import json

import mwa_metadb_utils as meta
import find_pulsar_in_obs as fpio


def getTargetAZZA(ra,dec,time,lat=-26.7033,lon=116.671,height=377.827):
    """
    Function to get the target position in alt/az at a given EarthLocation and Time.
    
    Default lat,lon,height is the centre of  MWA.

    Input:
      ra - target right ascension in astropy-readable format
      dec - target declination in astropy-readable format
      time - time of observation in UTC (i.e. a string on form: yyyy-mm-dd hh:mm:ss.ssss)
      lat - observatory latitude in degrees
      lon - observatory longitude in degrees

    Returns:
      a list containing four elements in the following order:
        list[0] = target azimuth in radians
        list[1] = target zenith angle in radians
        list[2] = target azimuth in degrees
        list[3] = target zenith angle in degrees
    """
    #print "Creating EarthLocation for: lat = {0} deg, lon = {1} deg".format(lat,lon)
    location = EarthLocation(lat=lat*u.deg, lon=lon*u.deg, height=height*u.m)
    
    #print "Creating SkyCoord for target at (RA,DEC) = ({0},{1})".format(ra,dec)
    coord = SkyCoord(ra,dec,unit=(u.hourangle,u.deg))
    #print "Target at: ({0},{1}) deg".format(coord.ra.deg,coord.dec.deg)
    
    obstime = Time(time)
    #print "Observation time: {0}".format(obstime.iso)
    
    #print "Converting to Alt,Az for given time and observatory location..."
    altaz = coord.transform_to(AltAz(obstime=obstime,location=location))
    #print "Target (Alt,Az) = ({0},{1}) deg".format(altaz.alt.deg,altaz.az.deg)
    
    #print "Converting to (Az,ZA)"
    az = altaz.az.rad 
    azdeg = altaz.az.deg
     
    za = np.pi/2 - altaz.alt.rad
    zadeg = 90 - altaz.alt.deg
    
    #print "Target (Az,ZA) = ({0},{1}) deg".format(azdeg,zadeg)

    return [az,za,azdeg,zadeg]
    
    
def getTargetradec(az,za,time,lst,lat=-26.7033,lon=116.671,height=377.827):
    """
    Function to get the target position in ra dec at a given EarthLocation and Time.
    
    Default lat,lon,height is the centre of  MWA.

    Input:
      az - target aximuth in radians
      za - target zenith in radians
      time - time of observation in UTC (i.e. a string on form: yyyy-mm-dd hh:mm:ss.ssss)
      lat - observatory latitude in degrees
      lon - observatory longitude in degrees

    Returns:
      a list containing four elements in the following order:
        list[0] = target ra in degrees
        list[1] = target dec in degrees
    """
    
    ha,dec = h2e(az,za,lat) #hour angle and dec in degrees
    ra = lst-ha
    

    return [ra,dec]
    
    
def two_floats(value):
    values = value.split()
    if len(values) != 2:
        raise argparse.ArgumentError
    return values  
    
    
#gird movements all in rad
def left(ra_in, dec_in, fwhm):
    dec_out = dec_in 
    ra_out = ra_in - fwhm/cos(dec_in)
    return [ra_out,dec_out]
    
def right(ra_in, dec_in, fwhm):
    dec_out = dec_in 
    ra_out = ra_in + fwhm/cos(dec_in)
    return [ra_out,dec_out]
    
def up(ra_in, dec_in, fwhm):
    dec_out = dec_in + fwhm / cos(dec_in + np.radians(26.7))**2
    ra_out = ra_in 
    return [ra_out,dec_out]

def down(ra_in, dec_in, fwhm):
    dec_out = dec_in - fwhm / cos(dec_in + np.radians(26.7))**2
    ra_out = ra_in 
    return [ra_out,dec_out]
  
def up_left(ra_in, dec_in, fwhm):
    half_fwhm_approx = fwhm/2.#/cos(dec_in)
    dec_out = dec_in + sin(np.radians(60.))*sin(half_fwhm_approx) / sin(np.radians(30.)) \
              / cos(dec_in + np.radians(26.7))**2
    ra_out = ra_in - fwhm/2./cos(dec_out)
    return [ra_out,dec_out]
    
def up_right(ra_in, dec_in, fwhm):
    half_fwhm_approx = fwhm/2.#/cos(dec_in)
    dec_out = dec_in + sin(np.radians(60.))*sin(half_fwhm_approx) / sin(np.radians(30.)) \
              / cos(dec_in + np.radians(26.7))**2
    ra_out = ra_in + fwhm/2./cos(dec_out)
    return [ra_out,dec_out]
    
def down_left(ra_in, dec_in, fwhm):
    half_fwhm_approx = fwhm/2.#/cos(dec_in)
    dec_out = dec_in - sin(np.radians(60.))*sin(half_fwhm_approx) / sin(np.radians(30.)) \
              / cos(dec_in + np.radians(26.7))**2
    ra_out = ra_in - fwhm/2./cos(dec_out)
    return [ra_out,dec_out]
    
def down_right(ra_in, dec_in, fwhm):
    half_fwhm_approx = fwhm/2.#/cos(dec_in)
    dec_out = dec_in - sin(np.radians(60.))*sin(half_fwhm_approx) / sin(np.radians(30.)) \
              / cos(dec_in + np.radians(26.7))**2
    ra_out = ra_in + fwhm/2./cos(dec_out)
    return [ra_out,dec_out]
    
    
def cross_grid(ra0,dec0,centre_fwhm, loop):
    #start location list [loop number][shape corner (6 for hexagon 4 for square)][number from corner]
    #each item has [ra,dec,fwhm] in radians
    pointing_list = [[[[ra0,dec0,centre_fwhm]]]]
    print "Calculating the tile positions"
    for l in range(loop):
        loop_temp = []
        for c in range(4):
            if l == 0:
                c = 0

            if c == 0:
                ra,dec =left(pointing_list[l][c][0][0],
                             pointing_list[l][c][0][1],centre_fwhm)
            elif c == 1:
                ra,dec =up(pointing_list[l][c][0][0],
                           pointing_list[l][c][0][1],centre_fwhm)
            elif c == 2:
                ra,dec =right(pointing_list[l][c][0][0],
                              pointing_list[l][c][0][1],centre_fwhm)
            elif c == 3:
                ra,dec =down(pointing_list[l][c][0][0],
                             pointing_list[l][c][0][1],centre_fwhm)
            loop_temp.append([[ra, dec]])
        pointing_list.append(loop_temp)
    return pointing_list


def hex_grid(ra0,dec0,centre_fwhm, loop):
    #start location list [loop number][shape corner (6 for hexagon 4 for square)][number from corner]
    #each item has [ra,dec,fwhm] in radians
    pointing_list = [[[[ra0,dec0,centre_fwhm]]]]
    print "Calculating the tile positions"

    for l in range(loop):
        #different step for each corner
        loop_temp = []
        for c in range(6):
            corner_temp = []
            if l == 0:
                c = 0
            for n in range(l + 1):
                if l == n: 
                    if l != 0:
                        #change the 2 for each loop
                        #uses next corner
                        if c == 0:
                            ra,dec =left(pointing_list[l][c+1][0][0],
                                         pointing_list[l][c+1][0][1],centre_fwhm)
                        elif c == 1:
                            ra,dec =up_left(pointing_list[l][c+1][0][0],
                                         pointing_list[l][c+1][0][1],centre_fwhm)
                        elif c == 2:
                            ra,dec =up_right(pointing_list[l][c+1][0][0],
                                         pointing_list[l][c+1][0][1],centre_fwhm)
                        elif c == 3:
                            ra,dec =right(pointing_list[l][c+1][0][0],
                                         pointing_list[l][c+1][0][1],centre_fwhm)
                        elif c == 4:
                            ra,dec =down_right(pointing_list[l][c+1][0][0],
                                         pointing_list[l][c+1][0][1],centre_fwhm)
                        elif c == 5:
                            ra,dec =down_left(pointing_list[l][0][0][0],
                                     pointing_list[l][0][0][1],centre_fwhm)
                    
                if l != n or l == 0:
                    if c == 0:
                        ra,dec =left(pointing_list[l][c][n][0],
                                     pointing_list[l][c][n][1],centre_fwhm)
                    elif c == 1:
                        ra,dec =up_left(pointing_list[l][c][n][0],
                                        pointing_list[l][c][n][1],centre_fwhm)
                    elif c == 2:
                        ra,dec =up_right(pointing_list[l][c][n][0],
                                         pointing_list[l][c][n][1],centre_fwhm)
                    elif c == 3:
                        ra,dec =right(pointing_list[l][c][n][0],
                                      pointing_list[l][c][n][1],centre_fwhm)
                    elif c == 4:
                        ra,dec =down_right(pointing_list[l][c][n][0],
                                           pointing_list[l][c][n][1],centre_fwhm)
                    elif c == 5:  
                        ra,dec =down_left(pointing_list[l][c][n][0],
                                          pointing_list[l][c][n][1],centre_fwhm)
                corner_temp.append([ra,dec])
            loop_temp.append(corner_temp)
        pointing_list.append(loop_temp)
    return pointing_list

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="""
    Makes a hexogonal grid pattern around a pointing for a MWA VCS observation.
    grid.py -o 1166459712 -p "06:25:31.20 -36:40:48.0" -d 0.6 -l 1
    """)
    parser.add_argument('-o', '--obsid',type=str,help='Observation ID')
    parser.add_argument('-p', '--pointing',type=two_floats,help='Centre pointing in hh:mm:ss.ss dd\"mm\'ss.ss')
    parser.add_argument('--aitoff',action="store_true",help='Plots the output in aitoff (may make it hard to analyise).')
    parser.add_argument('-f', '--fraction',type=float,help='Fraction of the full width half maximum to use as the distance between beam centres',default=0.85)
    parser.add_argument('-d', '--deg_fwhm',type=float,help='Sets the FWHM at zenith in degrees (best to test along dec). The script will not calculate the FWHM',default=0.3098)
    parser.add_argument('--dec_range_fwhm',type=float,nargs='+',help='A list of FWHM and ranges in the order of: "FWHM1 decmin1 decmax1 FWHM2 decmin2 decmax2"')
    parser.add_argument('-t', '--type',type=str,help='Can be put in either "hex" or "square" tiling mode. Default is hex.',default='hex')
    parser.add_argument('-l', '--loop',type=int,help='Number  of "loops" around the centre pointing the code will calculate. Default is 1',default=1)
    parser.add_argument('-a','--all_pointings',action="store_true",help='Will calculate all the pointings within the FWHM of the observations tile beam.')
    parser.add_argument('--dec_range',type=float,nargs='+',help='Dec limits: "decmin decmax". Default -90 90', default=[-90,90])
    parser.add_argument('--ra_range',type=float,nargs='+',help='RA limits: "ramin ramax". Default 0 390', default=[0,360])
    parser.add_argument('-v','--verbose_file',action="store_true",help='Creates a more verbose output file with more information than make_beam.c can handle.')

    args=parser.parse_args()

    opts_string = ""
    for k in args.__dict__:
        if args.__dict__[k] is not None:
            if k == "pointing":
                opts_string = opts_string + ' --' + str(k) + ' "' + str(args.__dict__[k][0]) +\
                         ' ' + str(args.__dict__[k][1]) + '"'
            else:
                opts_string = opts_string + ' --' + str(k) + ' ' + str(args.__dict__[k])
            
    obs, ra, dec, duration, xdelays, centrefreq, channels = meta.get_common_obs_metadata(args.obsid)
        
    #get fwhm in radians
    centre_fwhm = np.radians(args.deg_fwhm)

    #all_pointing parsing
    if (args.loop != 1) and args.all_pointings:
        print "Can't use --loop and --all_poinitings as all_pointings calculates the loops required. Exiting."
        quit()
    if args.pointing and args.all_pointings:
        print "Can't use --pointing and --all_poinntings as --all_pointings calculates the pointing. Exitting."
        quit()
    if args.all_pointings:
        #calculating loop number
        fudge_factor = 1.5
        tile_fwhm = np.degrees(1.22 * (3*10**8/(centrefreq*10**6))/6.56 )
        #account for the "increase" in tile beam size due to drifting
        tile_fwhm += duration/3600.*15.
        args.loop = int(tile_fwhm/2./(args.deg_fwhm*args.fraction))
        
        #calculating pointing from metadata
        ra = np.radians(ra + duration/3600.*15./2)
        dec = np.radians(dec)

    if not args.all_pointings:
        coord = SkyCoord(args.pointing[0],args.pointing[1],unit=(u.hourangle,u.deg))
        ra = coord.ra.radian #in radians
        dec = coord.dec.radian
    
    #calc grid positions
    if args.type == 'hex':
        pointing_list = hex_grid(ra, dec, centre_fwhm*args.fraction, 
                                 args.loop)
    elif args.type == 'cross':
        pointing_list = cross_grid(ra, dec, centre_fwhm*args.fraction,
                                   args.loop)
    else:
        print "Unrecognised grid type. Exiting."
        quit()
    #TODO add square

    time = Time(float(args.obsid),format='gps')
    ra_decs = []      
    ras = []; decs = []; theta = []; phi = []; rads = []; decds = []
    
    print "Converting ra dec to degrees"                
    for l in range(len(pointing_list)):
        for c in range(len(pointing_list[l])):
            for n in range(len(pointing_list[l][c])):
                #format grid pointings
                rad = np.degrees(pointing_list[l][c][n][0])
                decd = np.degrees(pointing_list[l][c][n][1])
                
                if decd > 90.:
                    decd = decd - 180.
                rads.append(rad)
                decds.append(decd)
                #ra_decs.append([rag,decg,az,za,rad,decd])
    
    if (args.dec_range or args.ra_range):
        print "Removing pointings outside of ra dec ranges"
        radls = []
        decdls = []
        for i in range(len(rads)):
            if  (args.dec_range[0] < float(decds[i]) < args.dec_range[1] ) and \
                (args.ra_range[0]  < float(rads[i]) < args.ra_range[1]):
                    radls.append(rads[i])
                    decdls.append(decds[i])
        rads = radls
        decds = decdls

    if args.all_pointings:
        #calculate powers
        obs_metadata = [obs, ra, dec, duration, xdelays, centrefreq, channels]
        names_ra_dec = []
        for ni in range(len(rads)):
            names_ra_dec.append(["name", rads[ni], decds[ni]])
        names_ra_dec = np.array(names_ra_dec)
        power = fpio.get_beam_power_over_time(obs_metadata,
                                              names_ra_dec, degrees=True)

        #check each pointing is within the tile beam
        radls = []
        decdls = []
        for ni in range(len(rads)):
            if max(power[ni]) > 0.5:
                radls.append(rads[ni])
                decdls.append(decds[ni])
        rads = radls
        decds = decdls
    
    print "Using skycord to convert ra dec"
    #Use skycoord to get asci
    coord = SkyCoord(rads,decds,unit=(u.deg,u.deg))
    #unformated
    rags_uf = coord.ra.to_string(unit=u.hour, sep=':')
    decgs_uf = coord.dec.to_string(unit=u.degree, sep=':')
    
    print "Formating the outputs"
    #format the ra dec strings 
    for i in range(len(rags_uf)):
        
        
        rag = rags_uf[i] 
        decg = decgs_uf[i]
        if len(rag) > 11:
            rag = rag[:11]
        if len(decg) > 12:
            decg = decg[:12]
            
        if len(rag) == 8:
            rag = rag + '.00'
        if len(decg) == 9:
            decg = decg + '.00'


        if args.verbose_file:
            az,za,azd,zad = getTargetAZZA(rag,decg,time)
        else:
            az,za,azd,zad = [0,0,0,0]
        
        ras.append(rag)
        decs.append(decg)
        theta.append(az)
        phi.append(za)
 
    if (args.dec_range or args.ra_range):
        #some ra and dec string for radec limited degreees
        print "Recording the dec limited poisitons in grid_positions_dec_limited.txt"            
        with open('grid_positions_ra_dec_limited_f'+str(args.fraction)+'_d'+str(args.deg_fwhm)+\
                  '_l'+str(args.loop) +'.txt','w') as out_file:
            if args.verbose_file:
                out_line = "#ra   dec    az     za\n" 
                out_file.write(out_line)
            for i in range(len(rads)):
                if args.verbose_file:
                    out_line = str(ras[i])+" "+str(decs[i])+" "+str(theta[i])+" "\
                                +str(phi[i])+" "+str(rads[i])+" "\
                                +str(decds[i])+"\n" 
                else:
                    out_line = str(ras[i])+" "+str(decs[i])+"\n" 
                out_file.write(out_line)
          
    else:    
        print "Recording the poisitons in grid_positions.txt"            
        with open('grid_positions_f'+str(args.fraction)+'_d'+str(args.deg_fwhm)+\
                  '_l'+str(args.loop)+'.txt','w') as out_file:
            if args.verbose_file:
                out_line = "#ra   dec    az     za\n" 
                out_file.write(out_line)
            for i in range(len(rads)):
                if args.verbose_file:
                    out_line = str(ras[i])+" "+str(decs[i])+" "+str(theta[i])+" "\
                                +str(phi[i])+" "+str(rads[i])+" "\
                                +str(decds[i])+"\n" 
                else:
                    out_line = str(ras[i])+" "+str(decs[i])+"\n" 

                out_file.write(out_line) 
               
           

    #matplotlib.use('Agg')
    print "Plotting"
    fig = plt.figure(figsize=(7, 7))
    if args.aitoff:
        fig.add_subplot(111)
        print "changing axis"
        ax = plt.axes(projection='mollweide')
        rads = -(np.radians(np.array(rads)))+ np.pi
        decds = np.radians(np.array(decds))
    else:
        plt.axes().set_aspect('equal')
        ax = plt.gca()

    plt.xlabel("ra (degrees)")
    plt.ylabel("dec (degrees)")
    
    for i in range(len(ras)):
        if args.aitoff:
            fwhm_circle = centre_fwhm/cos(decds[i]) / 2.
            circle = plt.Circle((rads[i],decds[i]),fwhm_circle,
                                color='r', lw=0.1,fill=False)
            ax.add_artist(circle)
        else:
            fwhm_vert = np.degrees(centre_fwhm/cos(np.radians(decds[i] + 26.7))**2)
            fwhm_horiz = np.degrees(centre_fwhm/cos(np.radians(decds[i])) )
            
            ellipse = patches.Ellipse((rads[i],decds[i]), fwhm_horiz, fwhm_vert,
                                          linewidth=0.2, fill=False, edgecolor='red')
            ax.add_patch(ellipse)
            #fwhm_circle = centre_fwhm/cos(np.radians(decds[i])) / 2.
            #circle = plt.Circle((rads[i],decds[i]),np.degrees(fwhm_circle),
            #                     color='r', lw=0.1,fill=False)
    plt.scatter(rads,decds,s=0.1,c='black')


    plt.savefig('grid_positions_'+str(args.obsid)+'_n'+str(len(rads))+'_f'+str(args.fraction)+\
                '_d'+str(args.deg_fwhm)+'_l'+str(args.loop)+'.png',bbox_inches='tight',\
                    dpi =1000)
        
       
        
    
    print "Number of pointings: " + str(len(rads))
    #times out and segfaults after this so I'm going to exit here
    exit()
   
    
   
