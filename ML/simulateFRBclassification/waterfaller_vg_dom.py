#!/usr/bin/env python

"""
waterfaller.py

Make waterfall plots to show frequency sweep of a single pulse.
Reads PSRFITS or SIGPROC filterbank format files.

Patrick Lazarus - Aug. 19, 2011
Paul Scholz - Nov 2015
Vishal Gajjar - Oct 2019 (dm vs time plot)

"""

#import sys
import optparse
import copy
import matplotlib
matplotlib.use('pdf')
import matplotlib.pyplot as plt
#SOF EDIT - fixes deprecation warning
#import matplotlib.cm
import numpy as np

from presto import psr_utils
from presto import rfifind

from presto import psrfits
from presto import filterbank
#import spectra
from scipy import stats
import pandas as pd
import os
import re



import pickle
import os
import pandas as pd
from sigpyproc.readers import FilReader
import sys

SWEEP_STYLES = ['r-', 'b-', 'g-', 'm-', 'c-']



def get_mask(rfimask, startsamp, N):
    """Return an array of boolean values to act as a mask
        for a Spectra object.

        Inputs:
            rfimask: An rfifind.rfifind object
            startsamp: Starting sample
            N: number of samples to read

        Output:
            mask: 2D numpy array of boolean values.
                True represents an element that should be masked.
    """
    sampnums = np.arange(startsamp, startsamp + N)
    blocknums = np.floor(sampnums / rfimask.ptsperint).astype('int')
    mask = np.zeros((N, rfimask.nchan), dtype='bool')
    for blocknum in np.unique(blocknums):
        blockmask = np.zeros_like(mask[blocknums == blocknum])
        chans_to_mask = rfimask.mask_zap_chans_per_int[blocknum]
        if chans_to_mask.any():
            blockmask[:, chans_to_mask] = True
        mask[blocknums == blocknum] = blockmask
    return mask.T


def maskfile(maskfn, data, start_bin, nbinsextra):
    rfimask = rfifind.rfifind(maskfn)
    mask = get_mask(rfimask, start_bin, nbinsextra)[::-1]
    masked_chans = mask.all(axis=1)
    # Mask data
    # data = data.masked(mask, maskval='median-mid80')
    data = data.masked(mask, maskval=0)

    # datacopy = copy.deepcopy(data)
    return data, masked_chans


def zap_channels(data, flag_chans):
    mask = np.zeros(data.data.shape, dtype=bool)
    masked_val = np.ones(data.data.shape[1], dtype=bool)
    if len(flag_chans) == 1:
        mask[flag_chans[0]] = masked_val
    elif len(flag_chans) == 2:
        mask[int(flag_chans[0]):int(flag_chans[1])] = masked_val

    new_data = data.masked(mask, maskval=0)
    return new_data


def waterfall(
        rawdatafile,
        start,
        duration,
        dm=None,
        nbins=None,
        nsub=None,
        subdm=None,
        zerodm=False,
        downsamp=1,
        scaleindep=False,
        width_bins=1,
        mask=False,
        maskfn=None,
        csv_file=None,
        bandpass_corr=False,
        ref_freq=None,
        zap_range=None,
        zap_original=None):

    # def waterfall(rawdatafile, start, duration, dm, nbins, nsub,\
    #              subdm, zerodm, downsamp=1, scaleindep,\
    #              width_bins=1, mask, maskfn, csv_file, bandpass_corr, \
    #              ref_freq, zap_range, zap_original):
    """
    Create a waterfall plot (i.e. dynamic specrum) from a raw data file.
    Inputs:
       rawdatafile - a PsrfitsData instance.
       start - start time of the data to be read in for waterfalling.
       duration - duration of data to be waterfalled.
    Optional Inputs:
       dm - DM to use when dedispersing data.
             Default: Don't de-disperse
       nbins - Number of time bins to plot. This option overrides
                the duration argument.
                Default: determine nbins from duration.
       nsub - Number of subbands to use. Must be a factor of number of channels.
               Default: Number of channels.
       subdm - DM to use when subbanding. Default: same as dm argument.
       zerodm - subtract mean of each time-sample from data before
                 de-dispersing.
       downsamp - Factor to downsample in time by. Default: Don't downsample.
       scaleindep - Scale each channel independently.
                     Default: Scale using global maximum.
       width_bins - Smooth each channel/subband with a boxcar width_bins wide.
                     Default: Don't smooth.
       maskfn - Filename of RFIFIND mask to use for masking data.
                 Default: Don't mask data.
       bandpass_corr - Correct for the bandpass. Requires an rfifind
                        mask provided by maskfn keyword argument.
                        Default: Do not remove bandpass.
       ref_freq - Reference frequency to de-disperse to.
                   If subbanding and de-dispersing the start time
                   will be corrected to account for change in
                   reference frequency.
                   Default: Frequency of top channel.
    Outputs:
       data - Spectra instance of waterfalled data cube.
       nbinsextra - number of time bins read in from raw data.
       nbins - number of bins in duration.
       start - corrected start time.
    """

    if subdm is None:
        subdm = dm

    # Read data
    if ref_freq is None:
        ref_freq = rawdatafile.freqs.max()

    if nsub and dm:
        df = rawdatafile.freqs[1] - rawdatafile.freqs[0]
        nchan_per_sub = rawdatafile.nchan / nsub
        top_ctrfreq = rawdatafile.freqs.max() - \
            0.5 * nchan_per_sub * df  # center of top subband
        # With heimdall start time is usually the arrival time at highest frequency so not sure if this needed
        # start += 4.15e3 * np.abs(1./ref_freq**2 - 1./top_ctrfreq**2) * dm

    try:
        source_name = rawdatafile.header['source_name']
    except BaseException:
        source_name = "Unknown"

    start_bin = np.round(start / rawdatafile.tsamp).astype('int')
    dmfac = 4.15e3 * \
        np.abs(1. / rawdatafile.frequencies[0]**2 - 1. / rawdatafile.frequencies[-1]**2)

    if nbins is None:
        nbins = np.round(duration / rawdatafile.tsamp).astype('int')

    if dm:
        nbinsextra = np.round((duration + dmfac * dm) /
                              rawdatafile.tsamp).astype('int')
    else:
        nbinsextra = nbins

    # If at end of observation
    if (start_bin + nbinsextra) > rawdatafile.nspec - 1:
        nbinsextra = rawdatafile.nspec - 1 - start_bin

    data = rawdatafile.get_spectra(start_bin, nbinsextra)
    # Masking
    if mask and maskfn:
        data, masked_chans = maskfile(maskfn, data, start_bin, nbinsextra)
    else:
        masked_chans = np.zeros(rawdatafile.nchan, dtype=bool)

    # Additional zapping -Z for original data (before subbanning)
    if zap_original:
        sub_grps = [
            x.group() for x in re.finditer(
                "(\\d+\\:\\d+|\\d+)+",
                zap_original)]

        for sub_str in sub_grps:
            temp_idx = np.array([])
            num_rows = data.data.shape[0]
            idx_match = re.match("(^\\d+)(\\d$)", sub_str)
            arr_match = re.match("(^\\d+)(:\\d*\\d$)", sub_str)
            if idx_match:
                temp_idx = np.array(
                    [int(idx_match.groups()[0] + idx_match.groups()[1])])
                if temp_idx[0] > (num_rows - 1):
                    print(
                        "Warning, zap channel index out of range. No zapping occurred.")
                    print((
                        "Zap channel must be within rows of length ",
                        num_rows))
                    temp_idx = np.array([])
            elif arr_match:
                temp_idx = np.sort(np.array([int(arr_match.groups()[0]),
                                             int(arr_match.groups()[1][1:])]))
                if temp_idx[-1] > (num_rows - 1):
                    print(
                        "Warning, zap channel index out of range. No zapping occurred.")
                    print((
                        "Zap channel must be within rows of length ",
                        num_rows))
                    temp_idx = np.array([])
            else:
                print("Warning zap channel input range invalid.")
            if len(temp_idx.shape) > 0:
                data = copy.deepcopy(zap_channels(data, temp_idx))

    # Bandpass correction
    if maskfn and bandpass_corr:
        bandpass = rfifind.rfifind(maskfn).bandpass_avg[::-1]
        # bandpass[bandpass == 0] = np.min(bandpass[np.nonzero(bandpass)])
        masked_chans[bandpass == 0] = True

    # ignore top and bottom 5% of band
    ignore_chans = int(np.ceil(0.05 * rawdatafile.nchan))
    masked_chans[:ignore_chans] = True
    masked_chans[-ignore_chans:] = True

    data_masked = np.ma.masked_array(data.data)
    data_masked[masked_chans] = np.ma.masked
    data.data = data_masked

    if bandpass_corr:
        data.data /= bandpass[:, None]

    # Zerodm filtering
    if (zerodm):
        data.data -= data.data.mean(axis=0)

    # This was original but getting wrong arrival time with sub-banding
    '''
    # Subband data
    if (nsub is not None) and (subdm is not None):
        data.subband(nsub, subdm, padval='mean')

    # Dedisperse
    if dm:
        data.dedisperse(dm, padval='rotate')
    '''

    # So doing dedispersion first and then sub-banding, problem now is that it will be very slow
    # Dedisperse
    if dm:
        data.dedisperse(dm, padval='rotate')

     # Subband data
    if (nsub is not None) and (subdm is not None):
        data.subband(nsub, subdm, padval='mean')

    # Downsample
    # Moving it after the DM-vs-time plot
    # data.downsample(downsamp)

    # Orignal scale has error if chan is flagged so writting this here
    if not scaleindep:
        std = data.data.std()
    for ii in range(data.numchans):
        chan = data.get_chan(ii)
        median = np.median(chan)
        if scaleindep:
            std = chan.std()
            if std:
                chan[:] = (chan - median) / std
            else:
                chan[:] = chan

    # scale data
    # data = data.scaled(scaleindep)
    # data = data.scaled(False)

    # Smooth
    if width_bins > 1:
        for ii in range(data.numchans):
            chan = data.get_chan(ii)
            nbin = len(chan)
            x = list(range(nbin))
            if (nbin > 4000):
                deg = 10
            if (nbin < 4000 and nbin > 2000):
                deg = 8
            if (nbin < 2000 and nbin > 1000):
                deg = 6
            if (nbin < 1000 and nbin > 150):
                deg = 5
            if (nbin < 150):
                deg = 2
            base = np.polyfit(x, chan, deg)
            p = np.poly1d(base)
            chan[:] = chan[:] - p(x)

    # Zap channels of data after subbaning, option '-z' or '--zap-chans'
    if zap_range:
        sub_grps = [
            x.group() for x in re.finditer(
                "(\\d+\\:\\d+|\\d+)+",
                zap_range)]

        for sub_str in sub_grps:
            temp_idx = np.array([])
            num_rows = data.data.shape[0]
            idx_match = re.match("(^\\d+)(\\d$)", sub_str)
            arr_match = re.match("(^\\d+)(:\\d*\\d$)", sub_str)
            if idx_match:
                temp_idx = np.array(
                    [int(idx_match.groups()[0] + idx_match.groups()[1])])
                if temp_idx[0] > (num_rows - 1):
                    print(
                        "Warning, zap channel index out of range. No zapping occurred.")
                    print((
                        "Zap channel must be within rows of length ",
                        num_rows))
                    temp_idx = np.array([])
            elif arr_match:
                temp_idx = np.sort(np.array([int(arr_match.groups()[0]),
                                             int(arr_match.groups()[1][1:])]))
                if temp_idx[-1] > (num_rows - 1):
                    print(
                        "Warning, zap channel index out of range. No zapping occurred.")
                    print((
                        "Zap channel must be within rows of length ",
                        num_rows))
                    temp_idx = np.array([])
            else:
                print("Warning zap channel input range invalid.")
            if temp_idx.shape > 0:
                data = copy.deepcopy(zap_channels(data, temp_idx))

    return data, nbinsextra, nbins, start, source_name


def plot_waterfall(
        data,
        start,
        source_name,
        duration,
        dm,
        ofile,
        integrate_ts=False,
        integrate_spec=False,
        show_cb=False,
        cmap_str="gist_yarg",
        sweep_dms=[],
        sweep_posns=[],
        ax_im=None,
        ax_ts=None,
        ax_spec=None,
        interactive=False,
        downsamp=1,
        nsub=None,
        subdm=None,
        width=None,
        snr=None,
        csv_file=None,
        prob=None,
        min_value=None,
        save_png=False):
    """ I want a docstring too!
    """

    if source_name is None:
        source_name = "Unknown"

    # Output file
    if ofile == "unknown_cand":
        title = "%s_" + ofile + "_%.3f_%s" % (source_name, start, str(dm))
    else:
        title = source_name + "_" + ofile

    # Set up axes
    fig = plt.figure(figsize=(10, 14))
    # fig.canvas.set_window_title("Frequency vs. Time")

    '''
    im_width = 0.6 if integrate_spec else 0.8
    im_height = 0.6 if integrate_ts else 0.8

    if not ax_im:
        ax_im = plt.axes((0.15, 0.15, im_width, im_height))
    if integrate_ts and not ax_ts:
        ax_ts = plt.axes((0.15, 0.75, im_width, 0.2),sharex=ax_im)

    if integrate_spec and not ax_spec:
        ax_spec = plt.axes((0.75, 0.15, 0.2, im_height),sharey=ax_im)
    '''

    ax_ts = plt.axes((0.1, 0.835, 0.71, 0.145))
    ax_im = plt.axes((0.1, 0.59, 0.71, 0.24), sharex=ax_ts)
    ax_dmvstm = plt.axes((0.1, 0.345, 0.71, 0.24), sharex=ax_ts)
    ax_spec = plt.axes((0.815, 0.59, 0.16, 0.24), sharey=ax_im)
    ax_dmsnr = plt.axes((0.815, 0.345, 0.16, 0.24), sharey=ax_dmvstm)
    ax_orig = plt.axes((0.1, 0.1, 0.71, 0.21))

    data.downsample(downsamp)
    nbinlim = int(duration / data.dt)

    # Get window
    spectrum_window = 0.02 * duration
    window_width = int(spectrum_window / data.dt)  # bins
    burst_bin = int(nbinlim / 2)

    # Zap all channels which have more than half bins zero (drop packets)
    zerochan = 1
    arrmedian = np.ones(data.numchans)
    if zerochan:
        for ii in range(data.numchans):
            chan = data.get_chan(ii)
            # if 50% are zero
            if len(chan) - np.count_nonzero(chan) > 0.5 * len(chan):
                arrmedian[ii] = 0.0
        # arrmedian=np.array(arrmedian)

    

    # Additional zapping from off-pulse spectra
    extrazap = 1
    zapthresh = 4
    if extrazap:
        # off-pulse from left
        off_spec1 = np.array(data.data[..., 0:burst_bin - window_width])
        # off-pulse from right
        off_spec2 = np.array(data.data[..., burst_bin + window_width:nbinlim])
        off_spec = (np.mean(off_spec1, axis=1) +
                    np.mean(off_spec2, axis=1)) / 2.0  # Total off-pulse
        mask = np.zeros(data.data.shape, dtype=bool)
        masked_val = np.ones(data.data.shape[1], dtype=bool)
        masked_chan = np.array(np.where(off_spec > zapthresh*np.std(off_spec)))[0]
        mask[masked_chan] = masked_val
        masked_chan1 = np.array(np.where(arrmedian == 0))[0]
        mask[masked_chan1] = masked_val
        data = data.masked(mask, maskval=0)
        for i in masked_chan:
            ax_spec.axhline(data.freqs[i], alpha=0.4, color='grey')
        for i in masked_chan1:
            ax_spec.axhline(data.freqs[i], alpha=0.4, color='grey')

    # DM-vs-time plot
    dmvstm_array = []
    # Old way
    '''
    lodm = int(dm-(dm*0.15))
    if lodm < 0: lodm = 0
    hidm = int(dm+(dm*0.15))
    '''
    band = (data.freqs.max() - data.freqs.min())
    centFreq = (data.freqs.min() + band / 2.0) / (10**3)  # To get it in GHz
    print(width, centFreq, band)
    # This comes from Cordes and McLaughlin (2003) Equation 13.
    FWHM_DM = 506 * float(width) * pow(centFreq, 3) / band
    # The candidate DM might not be exact so using a longer range
    FWHM_DM = 3.0 * FWHM_DM

    lodm = dm - FWHM_DM
    if lodm < 0:
        lodm = 0
        hidm = 2 * dm  # If low DM is zero then range should be 0 to 2*DM
    else:
        hidm = dm + FWHM_DM
    print(FWHM_DM, dm, lodm, hidm)
    dmstep = (hidm - lodm) / 48.0
    datacopy = copy.deepcopy(data)
    # print lodm,hidm
    for ii in np.arange(lodm, hidm, dmstep):
        # for ii in range(400,600,10):
        # Without this, dispersion delay with smaller DM step does not produce
        # delay close to bin width
        data.dedisperse(0, padval='rotate')
        data.dedisperse(ii, padval='rotate')
        Data = np.array(data.data[..., :nbinlim])
        Dedisp_ts = Data.sum(axis=0)
        dmvstm_array.append(Dedisp_ts)
    dmvstm_array = np.array(dmvstm_array)
    # print np.shape(dmvstm_array)
    # np.save('dmvstm_1step.npz',dmvstm_array)
    ax_dmvstm.set_xlabel("Time (sec) ")
    ax_dmvstm.set_ylabel("DM")
    # ax_dmvstm.imshow(dmvstm_array, aspect='auto', cmap=matplotlib.cm.cmap_d[cmap_str], origin='lower',extent=(data.starttime, data.starttime+ nbinlim*data.dt, lodm, hidm))
    ax_dmvstm.imshow(
        dmvstm_array,
        aspect='auto',
        origin='lower',
        extent=(
            data.starttime,
            data.starttime +
            nbinlim *
            data.dt,
            lodm,
            hidm))
    # cmap=matplotlib.cm.cmap_d[cmap_str])
    # interpolation='nearest', origin='upper')
    plt.setp(ax_im.get_xticklabels(), visible=False)
    plt.setp(ax_ts.get_xticklabels(), visible=False)
    ax_dmvstm.set_ylim(lodm, hidm)
    # extent=(data.starttime, data.starttime+ nbinlim*data.dt, 500, 700)
    # plt.show()
    # fig2 = plt.figure(2)
    # plt.imshow(dmvstm_array,aspect='auto')

    # Plot Freq-vs-time
    data = copy.deepcopy(datacopy)
    # data.downsample(downsamp)
    data.dedisperse(dm, padval='rotate')
    nbinlim = int(duration / data.dt)

    # pickle Spectra objects for prediction
    data.data = data.data[..., :nbinlim]
    with open(ofile + '.pickle', 'wb') as f:
        pickle.dump(data, f)

    # orig
    #img = ax_im.imshow(data.data[...,:nbinlim],aspect='auto',cmap=matplotlib.cm.cmap_d[cmap_str],interpolation='nearest',
    img = ax_im.imshow(data.data[...,:nbinlim],aspect='auto',cmap=plt.get_cmap(cmap_str),interpolation='nearest',
                       origin='upper',
                       extent=(data.starttime,
                               data.starttime + nbinlim * data.dt,
                               data.freqs.min(),
                               data.freqs.max()))
    # ax_im.axvline(x=(data.starttime + nbinlim*data.dt)/2.0,ymin=data.freqs.min(),ymax=data.freqs.max(),lw=3,color='b')
    if show_cb:
        cb = ax_im.get_figure().colorbar(img)
        cb.set_label("Scaled signal intensity (arbitrary units)")
    # Dressing it up
    ax_im.xaxis.get_major_formatter().set_useOffset(False)
    # ax_im.set_xlabel("Time")
    ax_im.set_ylabel("Frequency (MHz)")

    # Plot Time series
    # Data = np.array(data.data[..., :nbinlim])
    Data = np.array(data.data[..., :nbinlim])
    Dedisp_ts = Data.sum(axis=0)
    times = (np.arange(data.numspectra) * data.dt + start)[..., :nbinlim]
    ax_ts.plot(times, Dedisp_ts, "k")
    ax_ts.set_xlim([times.min(), times.max()])
    text1 = "DM: " + "%.2f" % float(data.dm)
    plt.text(
        1.1,
        0.9,
        text1,
        fontsize=15,
        ha='center',
        va='center',
        transform=ax_ts.transAxes)
    text2 = "Width: " + "%.2f" % float(width)
    plt.text(
        1.1,
        0.75,
        text2,
        fontsize=15,
        ha='center',
        va='center',
        transform=ax_ts.transAxes)
    text3 = "SNR: " + "%.2f" % float(snr)
    plt.text(
        1.1,
        0.6,
        text3,
        fontsize=15,
        ha='center',
        va='center',
        transform=ax_ts.transAxes)
    ax_ts.set_title(title, fontsize=14)
    plt.setp(ax_ts.get_xticklabels(), visible=False)
    plt.setp(ax_ts.get_yticklabels(), visible=False)

    # Spectrum and DM-vs-SNR plot
    ax_ts.axvline(times[burst_bin] - spectrum_window, ls="--", c="grey")
    ax_ts.axvline(times[burst_bin] + spectrum_window, ls="--", c="grey")

    # Get spectrum and DM-vs-SNR for the on-pulse window
    on_spec = np.array(
        data.data[..., burst_bin - window_width:burst_bin + window_width])
    on_dmsnr = np.array(
        dmvstm_array[..., burst_bin - window_width:burst_bin + window_width])
    Dedisp_spec = np.mean(on_spec, axis=1)
    Dedisp_dmsnr = np.mean(on_dmsnr, axis=1)

    # Get off-pulse and DM-vs-SNR for range outside on-pulse window
    # off-pulse from left
    off_spec1 = np.array(data.data[..., 0:burst_bin - window_width])
    # off-pulse from right
    off_spec2 = np.array(data.data[..., burst_bin + window_width:nbinlim])
    off_spec = (np.mean(off_spec1, axis=1) +
                np.mean(off_spec2, axis=1)) / 2.0  # Total off-pulse
    off_dmsnr1 = np.array(dmvstm_array[..., 0:burst_bin - window_width])
    off_dmsnr2 = np.array(dmvstm_array[..., burst_bin + window_width:nbinlim])
    off_dmsnr = (np.mean(off_dmsnr1, axis=1) +
                 np.mean(off_dmsnr2, axis=1)) / 2.0

    # Get Y-axis for both plots
    dms = np.linspace(lodm, hidm, len(Dedisp_dmsnr))
    freqs = np.linspace(data.freqs.max(), data.freqs.min(), len(Dedisp_spec))

    # Spectrum plot
    ax_spec.plot(Dedisp_spec, freqs, color="red", lw=2)
    ax_spec.plot(off_spec, freqs, color="blue", alpha=0.5, lw=1)
    ttest = float(stats.ttest_ind(Dedisp_spec, off_spec)[0].tolist())
    ttestprob = float(stats.ttest_ind(Dedisp_spec, off_spec)[1].tolist())
    text3 = "t-test"
    plt.text(
        1.1,
        0.45,
        text3,
        fontsize=12,
        ha='center',
        va='center',
        transform=ax_ts.transAxes)
    text4 = "  %.2f" % (ttest) + "(%.2f" % ((1 - ttestprob) * 100) + "%)"
    plt.text(
        1.1,
        0.3,
        text4,
        fontsize=12,
        ha='center',
        va='center',
        transform=ax_ts.transAxes)
    if prob:
        text5 = "ML prob: " + "%.2f" % (float(prob))
        print(text5)
        plt.text(
            1.1,
            0.1,
            text5,
            fontsize=12,
            ha='center',
            va='center',
            transform=ax_ts.transAxes)

    # DMvsSNR plot
    ax_dmsnr.plot(Dedisp_dmsnr, dms, color="red", lw=2)
    ax_dmsnr.plot(off_dmsnr, dms, color="grey", alpha=0.5, lw=1)
    Dedisp_dmsnr_split = np.array_split(Dedisp_dmsnr, 5)
    # Sub-array could be different sizes that's why
    Dedisp_dmsnr_split[0] = Dedisp_dmsnr_split[0].sum()
    Dedisp_dmsnr_split[1] = Dedisp_dmsnr_split[1].sum()
    Dedisp_dmsnr_split[2] = Dedisp_dmsnr_split[2].sum()
    Dedisp_dmsnr_split[3] = Dedisp_dmsnr_split[3].sum()
    Dedisp_dmsnr_split[4] = Dedisp_dmsnr_split[4].sum()

    # Plot settings
    plt.setp(ax_spec.get_xticklabels(), visible=True)
    plt.setp(ax_dmsnr.get_xticklabels(), visible=False)
    plt.setp(ax_spec.get_yticklabels(), visible=False)
    plt.setp(ax_dmsnr.get_yticklabels(), visible=False)
    ax_spec.set_ylim([data.freqs.min(), data.freqs.max()])
    ax_dmsnr.set_ylim(lodm, hidm)

    # Plot original data
    data.dedisperse(0, padval='rotate')
    ax_im.set_ylabel("Frequency (MHz)")
    ax_orig.set_ylabel("Frequency (MHz)")
    ax_orig.set_xlabel("Time (sec)")
    FTdirection = source_name.split("_")[0]

    if FTdirection == 'nT':
        ndata = data.data[..., ::-1]
        print("Will be flipped in Time")
    elif FTdirection == 'nF':
        ndata = data.data[::-1, ...]
        print("Will be flipped in freq")
    elif FTdirection == 'nTnF':
        ndata = data.data[::-1, ::-1]
        print("Will be flipped in time and freq")
    else:
        ndata = data.data
        print("No flip")

    # Sweeping it up
    for ii, sweep_dm in enumerate(sweep_dms):
        ddm = sweep_dm - data.dm

        # delays = psr_utils.delay_from_DM(ddm, data.freqs)
        # delays -= delays.min()

        if sweep_posns is None:
            sweep_posn = 0.0
        elif len(sweep_posns) == 1:
            sweep_posn = sweep_posns[0]
        else:
            sweep_posn = sweep_posns[ii]

        sweepstart = data.dt * data.numspectra * sweep_posn + data.starttime
        # sweepstart = data.dt*data.numspectra + data.starttime

        sty = "b-"

        if FTdirection == 'nT':
            ddm = (-1) * ddm  # Negative DM
            nfreqs = data.freqs
        elif FTdirection == 'nF':
            nfreqs = data.freqs[::-1]
        elif FTdirection == 'nTnF':
            ddm = (-1) * ddm  # Negative DM
            nfreqs = data.freqs[::-1]
        else:
            nfreqs = data.freqs

        delays = psr_utils.delay_from_DM(ddm, data.freqs)
        delays -= delays.min()
        ndelay = (delays + sweepstart)
        ndelay2 = (delays + sweepstart + duration)

        ax_orig.set_xlim(data.starttime, data.starttime +
                         len(data.data[0]) * data.dt)
        ax_orig.set_ylim(data.freqs.min(), data.freqs.max())
        ax_orig.plot(ndelay, nfreqs, "b-", lw=2, alpha=0.7)
        ax_orig.plot(ndelay2, nfreqs, "b-", lw=2, alpha=0.7)

    # Orig
    ax_orig.imshow(ndata,
                   aspect='auto',
                   cmap=plt.get_cmap(cmap_str),
                   interpolation='nearest',
                   origin='upper',
                   extent=(data.starttime,
                           data.starttime + len(data.data[0]) * data.dt,
                           data.freqs.min(),
                           data.freqs.max()))
    '''
    ax_orig.imshow(ndata, aspect='auto', \
        cmap=matplotlib.cm.cmap_d[cmap_str], \
        interpolation='nearest', origin='lower', \
        extent=(data.starttime, data.starttime + len(data.data[0])*data.dt, \
        data.freqs.min(), data.freqs.max()))
    '''
    # if interactive:
    #    fig.suptitle("Frequency vs. Time")
    #    fig.canvas.mpl_connect('key_press_event', \
    #            lambda ev: (ev.key in ('q','Q') and plt.close(fig)))
    # oname = "%.3f_%s.png" % (start,str(dm))

    if ofile == "unknown_cand":
        ofile = ofile + "_%.3f_%s.png" % (start, str(dm))

    ttestTrsh1 = 3
    ttestTrsh2 = 1
    probTrsh1 = 0.5
    probTrsh2 = 0.05
    DMleft = Dedisp_dmsnr_split[0]
    DMcent = Dedisp_dmsnr_split[2]
    DMright = Dedisp_dmsnr_split[4]

    #if prob >= probTrsh2:
    if prob is not None and prob >= probTrsh2:
        ofile = "A_" + ofile
        plt.text(
            1.1,
            0.2,
            "cat: A",
            fontsize=12,
            ha='center',
            va='center',
            transform=ax_ts.transAxes)
    elif ttest > ttestTrsh1 and DMcent > DMleft and DMcent > DMright:
        ofile = "B_" + ofile
        plt.text(
            1.1,
            0.2,
            "cat: B",
            fontsize=12,
            ha='center',
            va='center',
            transform=ax_ts.transAxes)
    elif ttest > ttestTrsh2 and DMcent > DMleft and DMcent > DMright:
        plt.text(
            1.1,
            0.2,
            "cat: C",
            fontsize=12,
            ha='center',
            va='center',
            transform=ax_ts.transAxes)
        ofile = "C_" + ofile

    if save_png:
        plt.savefig(ofile)
    # plt.show()
    return ofile, ttest, ttestprob


def main():
    fn = args[0]

    if fn.endswith(".fil"):
        # Filterbank file
        filetype = "filterbank"
        rawdatafile = filterbank.FilterbankFile(fn)
    elif fn.endswith(".fits"):
        # PSRFITS file
        filetype = "psrfits"
        rawdatafile = psrfits.PsrfitsFile(fn)
    else:
        raise ValueError("Cannot recognize data file type from "
                         "extension. (Only '.fits' and '.fil' "
                         "are supported.)")

    data, bins, nbins, start, source_name = waterfall(rawdatafile, options.start,
                                                      options.duration, dm=options.dm,
                                                      nbins=options.nbins, nsub=options.nsub,
                                                      subdm=options.subdm, zerodm=options.zerodm,
                                                      downsamp=options.downsamp,
                                                      scaleindep=options.scaleindep,
                                                      width_bins=options.width_bins, mask=options.mask,
                                                      maskfn=options.maskfile,
                                                      csv_file=options.csv_file,
                                                      bandpass_corr=options.bandpass_corr,
                                                      zap_range=options.zap_range,
                                                      zap_original=options.zap_original)

    ofile, ttest, ttestprob = plot_waterfall(data, start, source_name, options.duration,
                                             dm=options.dm, ofile=options.ofile, integrate_ts=options.integrate_ts,
                                             integrate_spec=options.integrate_spec, show_cb=options.show_cb,
                                             cmap_str=options.cmap, sweep_dms=options.sweep_dms,
                                             sweep_posns=options.sweep_posns, downsamp=options.downsamp,
                                             width=options.width, snr=options.snr, csv_file=options.csv_file, prob=options.prob,
                                             interactive=options.inter_plt)

    ttestprob = "%.2f" % ((1 - ttestprob) * 100)
    ttest = "%.2f" % (ttest)
    # Update CSV file if file is provided
    if csv_file:
        sourcename = rawdatafile.header['source_name']
        try:
            src_ra = rawdatafile.header['src_raj']
        except BaseException:
            src_ra = '00'
        try:
            src_dec = rawdatafile.header['src_dej']
        except BaseException:
            src_dec = "00"
        tstart = rawdatafile.header['tstart']
        fch1 = rawdatafile.header['fch1']
        nchans = rawdatafile.header['nchans']
        bw = int(rawdatafile.header['nchans']) * rawdatafile.header['foff']
        cat = ofile.split("_")[0]
        snr = options.snr
        width = options.width
        dm = options.dm
        if options.prob:
            prob = options.prob
        else:
            prob = "*"
        df = pd.DataFrame({'PNGFILE': [ofile],
                           'Category': [cat],
                           'Prob': [prob],
                           'T-test': [ttest],
                           'T-test_prob': [ttestprob],
                           'SNR': [snr],
                           'WIDTH': [width],
                           'DM': [dm],
                           'SourceName': [sourcename],
                           'RA': [src_ra],
                           'DEC': [src_dec],
                           'MJD': [tstart],
                           'Hfreq': [fch1],
                           'NCHANS': [nchans],
                           'BANDWIDTH': [bw],
                           'filename': [fn]})

        # Column order coming out irregular, so fixing it here
        col = [
            'PNGFILE',
            'Category',
            'Prob',
            'T-test',
            'T-test_prob',
            'SNR',
            'WIDTH',
            'DM',
            'SourceName',
            'RA',
            'DEC',
            'MJD',
            'Hfreq',
            'NCHANS',
            'BANDWIDTH',
            'filename']
        df = df.reindex(columns=col)

        if os.path.exists(csv_file) is False:
            with open(csv_file, 'w') as f:
                df.to_csv(f, header=True, index=False)
        else:
            with open(csv_file, 'a') as f:
                df.to_csv(f, header=False, index=False)


if __name__ == '__main__':
    parser = optparse.OptionParser(
        prog="waterfaller.py",
        version="v0.9 Patrick Lazarus (Aug. 19, 2011)",
        usage="%prog [OPTIONS] INFILE",
        description="Create a waterfall plot to show the "
        "frequency sweep of a single pulse "
        "in psrFits data.")
    parser.add_option('--subdm', dest='subdm', type='float',
                      help="DM to use when subbanding. (Default: "
                      "same as --dm)", default=None)
    parser.add_option(
        '-o',
        dest='ofile',
        default="unknown_cand",
        help="Output png plot file name (Default=start_dm)",
        type='str')
    parser.add_option(
        '--width',
        dest='width',
        default=None,
        help="Width of the pulse (for figure only; not used anywhere)",
        type='str')
    parser.add_option(
        '--snr',
        dest='snr',
        default=None,
        help="SNR of the pulse (for figure only; not used anywhere)",
        type='str')
    parser.add_option(
        "--prob",
        dest='prob',
        default=None,
        help="Probability of that candidate from ML tool",
        type='float')
    parser.add_option(
        '--zerodm',
        dest='zerodm',
        action='store_true',
        help="If this flag is set - Turn Zerodm filter - ON  (Default: "
        "OFF)",
        default=False)
    parser.add_option('-s', '--nsub', dest='nsub', type='int',
                      help="Number of subbands to use. Must be a factor "
                      "of number of channels. (Default: "
                      "number of channels)", default=None)
    parser.add_option('-d', '--dm', dest='dm', type='float',
                      help="DM to use when dedispersing data for plot. "
                      "(Default: 0 pc/cm^3)", default=0.0)
    parser.add_option('--show-ts', dest='integrate_ts', action='store_true',
                      help="Plot the time series. "
                      "(Default: Do not show the time series)", default=False)
    parser.add_option(
        '--show-spec',
        dest='integrate_spec',
        action='store_true',
        help="Plot the spectrum. "
        "(Default: Do not show the spectrum)",
        default=False)
    parser.add_option('--bandpass', dest='bandpass_corr', action='store_true',
                      help="Correct for the bandpass. Requires an rfifind "
                      "mask provided by --mask option."
                      "(Default: Do not remove bandpass)", default=False)
    parser.add_option('-T', '--start-time', dest='start', type='float',
                      help="Time into observation (in seconds) at which "
                      "to start plot.")
    parser.add_option('-t', '--duration', dest='duration', type='float',
                      help="Duration (in seconds) of plot.")
    parser.add_option('-n', '--nbins', dest='nbins', type='int',
                      help="Number of time bins to plot. This option takes "
                      "precedence over -t/--duration if both are "
                      "provided.")
    parser.add_option('--width-bins', dest='width_bins', type='int',
                      help="Smooth each channel/subband with a boxcar "
                      "this many bins wide. (Default: Don't smooth)",
                      default=1)
    parser.add_option('--sweep-dm', dest='sweep_dms', type='float',
                      action='append',
                      help="Show the frequency sweep using this DM. "
                      "(Default: Don't show sweep)", default=[])
    parser.add_option('--sweep-posn', dest='sweep_posns', type='float',
                      action='append',
                      help="Show the frequency sweep at this position. "
                      "The position refers to the high-frequency "
                      "edge of the plot. Also, the position should "
                      "be a number between 0 and 1, where 0 is the "
                      "left edge of the plot. "
                      "(Default: 0)", default=None)
    parser.add_option('--downsamp', dest='downsamp', type='int',
                      help="Factor to downsample data by. (Default: 1).",
                      default=1)
    parser.add_option('--maskfile', dest='maskfile', type='string',
                      help="Mask file produced by rfifind. Used for "
                      "masking and bandpass correction.",
                      default=None)
    # parser.add_option("--logs", action='store', dest='csv_file', type=str,
    # default='/home/vgajjar/PulsarSearch/example.csv',
    parser.add_option(
        "--logs",
        action='store',
        dest='csv_file',
        type=str,
        default='',
        help='Update results in the input CSV file')

    parser.add_option(
        '--mask',
        dest='mask',
        action="store_true",
        help="Mask data using rfifind mask (Default: Don't mask).",
        default=False)
    parser.add_option('--scaleindep', dest='scaleindep', action='store_true',
                      help="If this flag is set scale each channel "
                      "independently. (Default: Scale using "
                      "global maximum.)",
                      default=False)
    parser.add_option('--show-colour-bar', dest='show_cb', action='store_true',
                      help="If this flag is set show a colour bar. "
                      "(Default: No colour bar.)",
                      default=False)
    parser.add_option('--colour-map', dest='cmap',
                      help="The name of a valid matplotlib colour map."
                      "(Default: gist_yarg.)",
                      default='gist_yarg')
    parser.add_option(
        '-Z',
        dest='zap_original',
        type='string',
        help="Zaps the original frequency channel of the given index before subbaning data."
        "One channel is zapped if given single number 'n'. "
        "Range of channels is zapped if given range 'n:m'."
        "Seperate multiple sets with commas e.g 2,5,7:9",
        default=None)
    parser.add_option('-z', '--zap-chans', dest='zap_range', type='string',
                      help="Zaps the frequency channel of the given index. "
                      "One channel is zapped if given single number 'n'. "
                      "Range of channels is zapped if given range 'n:m'."
                      "Seperate multiple sets with commas e.g 2,5,7:9",
                      default=None)
    parser.add_option(
        '--ip',
        dest='inter_plt',
        help="Interactive mode for plotting with interactive_plot.py",
        default=False)
    parser.add_option('--save_png', dest='save_png', action='store_true',
                      help="Save waterfall plot to disk.",
                      default=False)
    options, args = parser.parse_args()

    if not hasattr(options, 'start'):
        raise ValueError("Start time (-T/--start-time) "
                         "must be given on command line!")
    if (not hasattr(options, 'duration')) and (not hasattr(options, 'nbins')):
        raise ValueError("One of duration (-t/--duration) "
                         "and num bins (-n/--nbins)"
                         "must be given on command line!")
    if options.subdm is None:
        options.subdm = options.dm

    # if options.csv_file:
    csv_file = options.csv_file

    main()
