from Ephemeris import Ephemeris
from Orbit import Orbit

import numpy

import auton
import ssd

import lsst.daf.persistence as dafPer



def selectOrbitsForFOV(dbLogicalLocation, 
                       sliceId, 
                       numSlices,
                       fovRA, 
                       fovDec, 
                       fovR, 
                       mjd):
    """
    Select from the orbit database those orbits that, at t=MJD, intersect the
    FOV (field of view) specified by (fovRA, fovDec) and whose size is given by
    fovR (which is the half width of the smallest circle enclosing the actual
    FOV).
    """
    # We want to select orbits that would intersect an area that is a bit bigger
    # than the FoV, just to be on the safe side. How much bigger? 
    # MaxErrorEllipseRadius bigger to take into account realistic positional 
    # errors of good orbits.
    MaxErrorEllipseRadius = 0.166 # ~1 arcminute in degrees
    
    # Fetch all known orbits and their ephemerides at midnight of the prev night
    # this night and next night.
    # orbitIdsAndPositions = [(orbitID, Ephemeris obj), ...]
    orbitIdsAndPositions = fetchOrbitIdsAndEphems(dbLogicalLocation, 
                                                  sliceId, 
                                                  numSlices, 
                                                  mjd, 
                                                  deltaMJD=1.)
    
    # Extract orbit_id, mjd, ra, dec.
    ephemData = [(oId, e.mjd, e.ra, e.dec) for (oId, e) in orbitIdsAndPositions]
    
    # Create a field structure. We simply need a number for field id.
    fields = [(0, mjd, fovRA, fovDec, fovR + MaxErrorEllipseRadius),] 
    
    # Invoke fieldProximity and get a {fieldID: [orbit_id, ...]} mapping of
    # orbits that intersect our field of view (which was given a fieldId = 0).
    mapping = auton.fieldproximity(fields=fields, orbits=ephemData, method=0)
    
    # Simply return the orbits corresponding to the IDs we got from 
    # fieldProximity.
    return([fetchOrbit(dbLogicalLocation, oid) for oid in mapping['0']])


def fetchOrbitIdsAndEphems(dbLogicalLocation, sliceId, numSlices, mjd, 
                           deltaMJD=1.):
    """
    Fetch the orbit Id of all known moving objects from day-MOPS together with
    their precomputed ephemerides at int(mjd)-deltaMJD, int(mjd) and
    int(mjd)+deltaMJD.
    
    @param dbLogicalLocation: pointer to the DB.
    @param sliceId: slice ID.
    @param numSlices: total number of slices.
    @param mjd: MJD of the exposure (UTC).
    @param deltaMJD: temporal distance betweeb successive ephemerides.

    Return
        [(internal_orbitId: Ephemeris obj), ] sorted by mjd
    """
    # Init the persistance middleware.
    db = dafPer.DbStorage()
    
    # Connect to the DB.
    loc = dafPer.LogicalLocation(dbLogicalLocation)
    db.setRetrieveLocation(loc)
    
    # Prepare the query.
    deltaMJD = abs(deltaMJD)
    mjdMin = mjd - deltaMJD
    mjdMax = mjd + deltaMJD
    
    # TODO: handle different MovingObject versions. Meaning choose the highest
    # version. Not needed for DC3a.
    where = 'mjd >= %f and mjd <= %f and ' %(mjdMin, mjdMax)
    # Poor man parallelism ;-)
    where += 'movingObjectId % %d = %d' %(numSlices, sliceId)
    
    db.startTransaction()
    db.setTableForQuery('_tmpl_mops_Ephemeris')
    db.setQueryWhere(where)
    db.outColumn('movingObjectId')
    db.outColumn('movingObjectVersion')
    db.outColumn('mjd')
    db.outColumn('ra_deg')
    db.outColumn('dec_deg')
    db.outColumn('mag')
    db.outColumn('smaa')
    db.outColumn('smia')
    db.outColumn('pa')
    db.orderBy('movingObjectId')
    db.orderBy('mjd')
    
    # Execute the query.
    db.query()

    # Fetch the results.
    res = []
    while db.next():
        ephem = Ephemeris(db.getColumnByPosInt64(0),     # movingObjectId
                          db.getColumnByPosInt64(1),     # movingObjectVersion
                          db.getColumnByPosDouble(2),    # mjd
                          db.getColumnByPosDouble(3),    # ra_deg
                          db.getColumnByPosDouble(4),    # dec_deg
                          db.getColumnByPosDouble(5),    # mag
                          db.getColumnByPosDouble(6),    # smaa
                          db.getColumnByPosDouble(7),    # smia
                          db.getColumnByPosDouble(8))    # pa
        # We now create a new temp id made by concatenating the movingobject id 
        # and its version. It will only be used internally.
        # res= [(new_orbit_id, Ephemeris obj), ...]
        res.append(('%d-%d' %(db.getColumnByPosInt64(0), 
                              db.getColumnByPosInt64(0)),
                    ephem))
    # We are done with the query.
    db.finishQuery()
    return(res)
    

def fetchOrbit(dbLogicalLocation, orbitId):
    """
    Fetch the full Orbit corresponding to the internal orbitId:
        orbitId = '%d-%d' %(movingObjectId, movingObjectVersion)
    
    @param dbLogicalLocation: pointer to the DB.
    @param orbitId: orbit ID.
    
    Return
        Orbit obj
    """
    # Init the persistance middleware.
    db = dafPer.DbStorage()
    
    # Connect to the DB.
    loc = dafPer.LogicalLocation(dbLogicalLocation)
    db.setRetrieveLocation(loc)
    
    # Remember that we defined a new internal orbitId as the concatenation of
    # movingObjectId and movingObjectVersion: 
    # orbitId = '%d-%d' %(movingObjectId, movingObjectVersion)
    (movingObjectId, movingObjectVersion) = orbitId.split('-')
    
    # Prepare the query.
    where = 'movingObjectId=%s and movingObjectVersion=%s' \
            %(movingObjectId, movingObjectVersion)
    db.startTransaction()
    db.setTableForQuery('MovingObject')
    db.setQueryWhere(where)
    cols = ['q', 'e', 'i', 'node', 'argPeri', 'timePeri', 'epoch', 'h_v', 'g']
    cols += ['src%02d' %(i) for i in range(1, 22, 1)]
    errs = map(lambda c: db.outColumn(c), cols)
    
    # Execute the query.
    db.query()
    
    # Create the Orbit object and just spit it out.
    elements = [db.getColumnByPosDouble(i) for i in range(0, 9)]
    src = [db.getColumnByPosDouble(i) for i in range(9, 30)]
    
    # We are done with the query.
    db.finishQuery()
    
    args = [int(movingObjectId), int(movingObjectVersion), ] + elements + [src]
    return(Orbit(*args))


def _isinside(e, fovRA, fovDec, fovR):
    """
    Return True if the Ephemeris object e in inside the FoV defined by fovRA, 
    fovDec and fovR. False otherwhise.
    """
    # TODO: Implememt something here!
    return(True)


def propagateOrbit(orbit, mjd, obscode):
    """
    Compute the ephemerides for orbit orbit at time mjd from obscode.

    Return
        [RA, Dec, mag, mjd, smaa, smia, pa]

        RA: Right Ascension (deg).
        Dec: Declination (deg).
        mag: apparent magnitude (mag).
        mjd: input ephemerides date time (UTC MJD).
        smaa: error ellipse semi major axis (deg).
        smia: error ellipse semi minor axis (deg).
        pa: error ellipse position angle (deg).
    """
    # Extract the orbital params and cast them into a numpy array.
    orbitalParams = numpy.array([orbit.q,
                                 orbit.e,
                                 orbit.i,
                                 orbit.node,
                                 orbit.argPeri,
                                 orbit.timePeri])
    if(None in list(orbit.src)):
        orbit.src = None

    # positions = [[RA, Dec, mag, mjd, raerr, decerr, smaa, smia, pa], ]
    ephems = ssd.ephemerides(orbitalParams, 
                             float(orbit.epoch), 
                             numpy.array([mjd, ]), 
                             str(obscode),
                             float(orbit.hv), 
                             float(orbit.g), 
                             orbit.src)
    (ra, dec, mag, predMjd, raErr, decErr, smaa, smia, pa) = ephems[0]
    
    # Return the Ephemeris object.
    return(Ephemeris(orbit.movingObjectId, 
                     orbit.movingObjectVersion, 
                     predMjd, 
                     ra, 
                     dec, 
                     mag, 
                     smaa, 
                     smia, 
                     pa))
    





