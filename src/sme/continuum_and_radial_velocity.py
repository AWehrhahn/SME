"""
Determine continuum based on continuum mask
and fit best radial velocity to observation
"""

import logging
import warnings

import numpy as np
from scipy.signal import correlate
from scipy.interpolate import interp1d
from scipy.optimize import least_squares
from scipy.constants import speed_of_light


c_light = speed_of_light * 1e-3  # speed of light in km/s


def determine_continuum(sme, segment):
    """
    Fit a polynomial to the spectrum points marked as continuum
    The degree of the polynomial fit is determined by sme.cscale_flag

    Parameters
    ----------
    sme : SME_Struct
        input sme structure with sme.sob, sme.wave, and sme.mob
    segment : int
        index of the wavelength segment to use, or -1 when dealing with the whole spectrum

    Returns
    -------
    cscale : array of size (ndeg + 1,)
        polynomial coefficients of the continuum fit, in numpy order, i.e. largest exponent first
    """

    if segment < 0:
        return sme.cscale

    if "sob" not in sme or "mob" not in sme or "wave" not in sme or "uob" not in sme:
        # If there is no observation, we have no continuum scale
        warnings.warn("Missing data for continuum fit")
        cscale = None
    elif sme.cscale_flag < 0:
        # Continuum flag is set to no continuum
        cscale = sme.cscale
        cscale = cscale[segment] if len(cscale) > 1 else cscale[0]
    else:
        # fit a line to the continuum points
        ndeg = sme.cscale_flag

        # Extract points in this segment
        x, y, m, u = sme.spectrum(return_uncertainty=True, return_mask=True)
        x = x[segment]
        y = y[segment]
        u = u[segment]
        m = m[segment]

        # Set continuum mask
        cont = (m == 2) & (u != 0)
        x = x[cont]
        y = y[cont]
        u = u[cont]

        # Fit polynomial
        cscale = np.polyfit(x, y, deg=ndeg, w=1 / u)

    logging.debug("Continuum coefficients for segment %i: %s", segment, cscale)
    return cscale


def determine_radial_velocity(sme, segment, cscale, x_syn, y_syn):
    """
    Calculate radial velocity by using cross correlation and
    least-squares between observation and synthetic spectrum

    Parameters
    ----------
    sme : SME_Struct
        sme structure with observed spectrum and flags
    segment : int
        which wavelength segment to handle, -1 if its using the whole spectrum
    cscale : array of size (ndeg,)
        continuum coefficients, as determined by e.g. determine_continuum
    x_syn : array of size (n,)
        wavelength of the synthetic spectrum
    y_syn : array of size (n,)
        intensity of the synthetic spectrum

    Raises
    ------
    ValueError
        if sme.vrad_flag is not recognized

    Returns
    -------
    rvel : float
        best fit radial velocity for this segment/whole spectrum
        or None if no observation is present
    """

    if "sob" not in sme or "mob" not in sme or "wave" not in sme or "uob" not in sme:
        # No observation no radial velocity
        warnings.warn("Missing data for radial velocity determination")
        rvel = None
    elif sme.vrad_flag in [-2, "none"]:
        # vrad_flag says don't determine radial velocity
        rvel = np.atleast_1d(sme.vrad)
        rvel = rvel[segment] if len(rvel) > 1 else rvel[0]
    elif sme.vrad_flag in [-1, "whole"] and segment >= 0:
        # We are inside a segment, but only want to determine rv at the end
        rvel = 0
    else:
        # Fit radial velocity
        # Extract data
        x, y, m, u = sme.spectrum(return_uncertainty=True, return_mask=True)
        if sme.vrad_flag in [0, "each"]:
            # Only this one segment
            x_obs = x[segment]
            y_obs = y[segment]
            u_obs = u[segment]
            mask = m[segment]

            # apply continuum
            if cscale is not None:
                cont = np.polyval(cscale, x_obs)
            else:
                warnings.warn(
                    "No continuum scale passed to radial velocity determination"
                )
                cont = np.ones_like(y_obs)

            y_obs = y_obs / cont

        elif sme.vrad_flag in [-1, "whole"]:
            # All segments
            if cscale is not None:
                cscale = np.atleast_2d(cscale)
                cont = [np.polyval(c, x[i]) for i, c in enumerate(cscale)]
            else:
                warnings.warn(
                    "No continuum scale passed to radial velocity determination"
                )
                cont = [1 for _ in range(len(x))]

            y = y.copy()
            for i in range(len(x)):
                y[i] = y[i] / cont[i]

            x_obs = x.__values__
            y_obs = y.__values__
            u_obs = u.__values__
            mask = m.__values__
        else:
            raise ValueError(
                f"Radial velocity flag {sme.vrad_flag} not recognised, expected one of 'each', 'whole', 'none'"
            )

        y_tmp = np.interp(x_obs, x_syn, y_syn)

        # Get a first rough estimate from cross correlation
        # Subtract continuum level of 1, for better correlation
        corr = correlate(
            y_obs - np.median(y_obs), y_tmp - np.median(y_tmp), mode="same"
        )
        offset = np.argmax(corr)

        x1 = x_obs[offset]
        x2 = x_obs[len(x_obs) // 2]
        rvel = c_light * (1 - x2 / x1)

        lines = (mask == 1) & (u_obs != 0)

        # Then minimize the least squares for a better fit
        # as cross correlation can only find
        def func(rv):
            rv_factor = np.sqrt((1 - rv / c_light) / (1 + rv / c_light))
            shifted = interpolator(x_obs[lines] * rv_factor)
            # shifted = np.interp(x_obs[lines], x_syn * rv_factor, y_syn)
            resid = (y_obs[lines] - shifted) / u_obs[lines]
            return resid

        interpolator = interp1d(x_syn, y_syn, kind="cubic")
        res = least_squares(func, x0=rvel, loss="soft_l1")
        rvel = res.x[0]

    logging.debug("Radial velocity for segment %i: %f", segment, rvel)
    return rvel


def match_rv_continuum(sme, segment, x_syn, y_syn):
    """
    Match both the continuum and the radial velocity of observed/synthetic spectrum

    Note that the parameterization of the continuum is different to old SME !!!

    Parameters
    ----------
    sme : SME_Struct
        input sme structure with all the parameters
    segment : int
        index of the wavelength segment to match, or -1 when dealing with the whole spectrum
    x_syn : array of size (n,)
        wavelength of the synthetic spectrum
    y_syn : array of size (n,)
        intensitz of the synthetic spectrum

    Returns
    -------
    rvel : float
        new radial velocity
    cscale : array of size (ndeg + 1,)
        new continuum coefficients
    """

    cscale = determine_continuum(sme, segment)
    rvel = determine_radial_velocity(sme, segment, cscale, x_syn, y_syn)

    return cscale, rvel