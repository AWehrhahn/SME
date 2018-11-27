import numpy as np
from scipy.optimize import least_squares
from scipy.signal import correlate
from scipy.optimize import minimize
from scipy.constants import speed_of_light

from .bezier import bezier_interp

c_light = speed_of_light * 1e-3  # speed of light in km/s


def determine_continuum(sme, segment):
    if segment < 0:
        return sme.cscale

    if "sob" not in sme:
        # If there is no observation, we have no continuum scale
        cscale = None
    elif sme.cscale_flag < 0:
        # Continuum flag is set to no continuum
        cscale = sme.cscale
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
        # Inverse coefficient order to make it more intuitive ?
        # cscale = cscale_new[::-1]

    return cscale


def determine_radial_velocity(sme, segment, cscale, x_syn, y_syn):
    if "sob" not in sme:
        # No observation no radial velocity
        rvel = None
    elif sme.vrad_flag < 0:
        # vrad_flag says don't determine radial velocity
        rvel = sme.rvel
    else:
        # Fit radial velocity
        # Extract data
        x, y, m, u = sme.spectrum(return_uncertainty=True, return_mask=True)
        if sme.vrad_flag > 0 and segment >= 0:
            # Only this one segment
            x_obs = x[segment]
            y_obs = y[segment]
            u_obs = u[segment]
            mask = m[segment]

            # apply continuum
            if cscale is not None:
                cont = np.polyval(cscale, x_obs)
            else:
                print(
                    "Warning: No continuum scale passed to radial velocity determination"
                )
                cont = np.ones_like(y_obs)

            y_obs = y_obs / cont

        else:
            # All segments
            x_obs = x.__values__
            y_obs = y.__values__
            u_obs = u.__values__
            mask = m.__values__

            if cscale is not None:
                cont = [np.polyval(c, x[segment]) for c in cscale]
            else:
                print(
                    "Warning: No continuum scale passed to radial velocity determination"
                )
                cont = [1 for _ in range(len(x))]

            for i in range(len(x)):
                y_obs[i] = y_obs[i] / cont[i]

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
            tmp = np.interp(x_obs[lines] * (1 - rv / c_light), x_syn, y_syn)
            return np.sum((y_obs[lines] - tmp) ** 2 * u_obs[lines] ** -2)

        res = minimize(func, x0=rvel)
        rvel = res.x[0]

    return rvel


def match_rv_continuum(sme, segment, x_syn, y_syn):
    """
    Match both the continuum and the radial velocity of observed/synthetic spectrum

    Note that the parameterization of the continuum is different to old SME !!!

    Parameters
    ----------
    x_obs : array
        observed wavelength
    y_obs : array
        observed flux
    u_obs : array
        uncertainties of observed flux
    x_syn : array
        synthetic wavel
    y_syn : array
        synthetic flux
    mask : array
        pixel mask, determining continuum and lines (continuum == 2, line == 1, bad == 0)
    ndeg : int, optional
        number of degrees of the continuum polynomial (default: 1, i.e linear)
    rvel : float, optional
        radial velocity guess (not used unless fix_rv is True) (default: 0)
    cscale : array[ndeg], optional
        continuum polynomial coefficients (not used unless fix_c is True) (default: None)
    fix_c : bool, optional
        use old continuum instead of recalculating it (default: False)
    fix_rv : bool, optional
        use old radial velocity instead of recalculating it (default: False)

    Returns
    -------
    rvel : float
        new radial velocity
    cscale : array[ndeg]
        new continuum coefficients
    """

    cscale = determine_continuum(sme, segment)
    rvel = determine_radial_velocity(sme, segment, cscale, x_syn, y_syn)

    return cscale, rvel
