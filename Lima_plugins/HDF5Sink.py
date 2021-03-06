#!/usr/bin/env python
# coding: utf8
from __future__ import with_statement, print_function
"""
LImA ProcessLib example of HDF5 writer 

This depends on PyFAI and on h5py

"""
__author__ = "Jérôme Kieffer"
__contact__ = "Jerome.Kieffer@ESRF.eu"
__license__ = "GPLv3+"
__copyright__ = "European Synchrotron Radiation Facility, Grenoble, France"
__date__ = "12/12/2013"
__status__ = "beta"
__docformat__ = 'restructuredtext'

import os, json, threading, logging, posixpath, time, types
logger = logging.getLogger("lima.hdf5")
# set loglevel at least at INFO
if logger.getEffectiveLevel() > logging.INFO:
    logger.setLevel(logging.INFO)
import numpy
from Lima import Core
from pyFAI.io import getIsoTime
import h5py

class StartAcqCallback(Core.SoftCallback):
    """
    Class managing the connection from a 
    Lima.Core.CtControl.prepareAcq() to the configuration of the various tasks
    
    Example of usage:
    cam = Basler.Camera(ip)
    iface = Basler.Interface(cam)
    ctrl = Core.CtControl(iface)
    processLink = LinkPyFAI(worker, writer)
    extMgr = ctrl.externalOperation()
    myOp = self.extMgr.addOp(Core.USER_LINK_TASK, "pyFAILink", 0)
    myOp.setLinkTask(processLink)
    callback = StartAcqCallback(ctrl, processLink)
    myOp.registerCallback(callback)
    acq.setAcqNbFrames(0)
    acq.setAcqExpoTime(1.0)
    ctrl.prepareAcq() #Configuration called here !!!!
    ctrl.startAcq()

    """
    CONFIG_ITEMS = {"dimX": None,
                    "dimY":None,
                    "binX": None,
                    "binY":None,
                    "directory": None,
                    "prefix": None,
                    "start_index": None,
                    "number_of_frames":None,
                    "exposure_time": None,
                     }
    LIMA_DTYPE = {Core.Bpp10:   "uint16",
                  Core.Bpp10S:   "int16",
                  Core.Bpp12:   "uint16",
                  Core.Bpp12S:   "int16",
                  Core.Bpp14:   "uint16",
                  Core.Bpp14S:   "int16",
                  Core.Bpp16:   "uint16",
                  Core.Bpp16S:   "int16",
                  Core.Bpp32:   "uint32",
                  Core.Bpp32S:   "int32",
                  Core.Bpp8:    "uint8",
                  Core.Bpp8S:   "int8"
                  }
    LIMA_ROTATION = {Core.Rotation_0: "no_rot",
                     Core.Rotation_90: "rot_90_cw",
                     Core.Rotation_270: "rot_90_ccw",
                     Core.Rotation_180: "rot_180",
                     }
    def __init__(self, control, task=None):
        """
        
        @param control: Lima.Core.CtControl instance
        @param task: The task one wants to parametrize at startup. Can be a  Core.Processlib.LinkTask or a Core.Processlib.SinkTask
        """
        Core.SoftCallback.__init__(self)
        self._control = control
        self._task = task

    def prepare(self):
        """
        Called with prepareAcq()
        """

        im = self._control.image()
        imdim = im.getImageDim().getSize()
        x = imdim.getWidth()
        y = imdim.getHeight()
        bin = im.getBin()
        binX = bin.getX()
        binY = bin.getY()
        flip = im.getFlip()
        roi = im.getRoi()
        lima_cfg = {"dimX":x,
                    "dimY":y,
                    "binX":binX,
                    "binY":binY,
                    "flipX":flip.x,
                    "flipY":flip.y,
                    "rotation":self.LIMA_ROTATION[im.getRotation()],
                    "mode": im.getMode(),
                    "dtype": self.LIMA_DTYPE[im.getImageType()]}
        if roi.isActive():
            lima_cfg["OffsetX"] = roi.getTopLeft().x
            lima_cfg["OffsetY"] = roi.getTopLeft().y
        saving = self._control.saving()
        sav_parms = saving.getParameters()
        lima_cfg["directory"] = sav_parms.directory
        lima_cfg["prefix"] = sav_parms.prefix
        lima_cfg["start_index"] = sav_parms.nextNumber
        lima_cfg["indexFormat"] = sav_parms.indexFormat
        # number of images ...
        acq = self._control.acquisition()
        lima_cfg["number_of_frames"] = acq.getAcqNbFrames() #to check.
        lima_cfg["exposure_time"] = acq.getAcqExpoTime()
        #ROI see: https://github.com/esrf-bliss/Lima/blob/master/control/include/CtAcquisition.h
        #https://github.com/esrf-bliss/Lima-tango/blob/master/LimaCCDs.py line 400
        #controle include ctsavings #154 ->
        #other metadata ctsaving
        if self._task._writer:
#            print(lima_cfg)
            self._task._writer.init(lima_cfg=lima_cfg)
            self._task._writer.flush()


class HDF5Sink(Core.Processlib.SinkTaskBase):
    """
    This is a ProcessLib task which is a sink: 
    it saves the image into a HDF5 stack.
    
    """

    def __init__(self, writer=None):
        Core.Processlib.SinkTaskBase.__init__(self)
#        self._config = {}
        if writer is None:
            logger.error("Without a writer, SinkPyFAI will just dump all data to /tmp")
            self._writer = HDF5Writer(filename="/tmp/LImA_default.h5")
        else:
            self._writer = writer

    def __repr__(self):
        """
        pretty print of myself
        """
        lstout = [ "HDF5Sink instance", "Writer:", self._writer.__repr__()]
        return os.linesep.join(lstout)


    def process(self, data) :
        """
        Callback function
        
        Called for every frame in a different C++ thread.
        """
        logger.debug("in Sink.process")
        if self._writer: #optional HDF5 writer
            self._writer.write(data.buffer, data.frameNumber)
        else:
            logger.warning("No writer defined !!!")


    def setConfig(self, config_dict=None):
        """
        Set the "static" configuration like filename and so on.
        
        @param config_dict: dict or json-serialized dict or file containing this dict.
        """
        self._writer.setConfig(jsonconfig)

class HDF5Writer(object):
    """
    Class allowing to write HDF5 Files.    
    """
    CONFIG_ITEMS = {"filename": None,
                    "dirname":None,
                    "extension": ".h5",
                    "subdir":None,
                    "hpath": "entry_",
                    "lima_grp": "LImA_DATA",
                    "dataset_name": "data",
                    "compression": None,
                    "min_size":1,
                    "detector_name": "LImA Detector",
                    "metadata_grp": "detector_config"
                     }

    def __init__(self, **config):
        """
        Constructor of an HDF5 writer:
        
        @param filename: name of the file
        @param hpath: name of the group: it will contain data (2-4D dataset), [tth|q|r] and pyFAI, group containing the configuration
        @param burst: exprected size of the dataset 
        """
        self._sem = threading.Semaphore()
        self._initialized = False
        self.filename = self.dirname = self.extension = self.subdir = self.hpath = self.lima_grp =None
        self.dataset_name = self.compression = self.min_size = self.detector_name = self.metadata_grp = None
        for kw, defval in self.CONFIG_ITEMS.items():
            self.__setattr__(kw, config.get(kw, defval))
        self.hdf5 = None
        self.group = None
        self.dataset = None
        self.chunk = None
        self.shape = None

    def __repr__(self):
        out = ["HDF5 writer  %sinitialized" % ("" if self._initialized else "un")] + \
        ["%s: %s" % (k, self.__getattribute__(k)) for k in self.CONFIG_ITEMS]
        return os.linesep.join(out)

    def init(self, lima_cfg=None):
        """
        Initializes the HDF5 file for writing. Part of prepareAcq.
        
        @param lima_cfg: dictionnary with parameters coming from Lima at the "prepareAcq" 
        """
        logger.debug("HDF5 writer init with %s" % lima_cfg)
        with self._sem:
            if h5py:
                try:
                    self.hdf5 = h5py.File(self.filename)
                except IOError:
                    logger.error("typically a corrupted HDF5 file ! : %s" % self.filename)
                    os.unlink(self.filename)
                    self.hdf5 = h5py.File(self.filename)
            else:
                err = "No h5py library, no chance"
                logger.error(err)
                raise RuntimeError(err)
            prefix = lima_cfg.get("prefix") or self.CONFIG_ITEMS["hpath"]
            if not prefix.endswith("_"):
                prefix+="_"
            entries = len([i.startswith(prefix) for i in self.hdf5])
            self.hpath = posixpath.join("%s%04d"%(prefix,entries),self.lima_grp)
            self.group = self.hdf5.require_group(self.hpath)
            self.group.parent.attrs["NX_class"] = "NXentry"
            self.group.attrs["NX_class"] = "NXdata"
            cfg_grp = self.hdf5.require_group(posixpath.join(self.hpath, self.metadata_grp))
            cfg_grp["detector_name"] = numpy.string_(self.detector_name)
            for k, v in lima_cfg.items():
                if type(v) in types.StringTypes:
                    cfg_grp[k] = numpy.string_(v)
                else:
                    cfg_grp[k] = v
            number_of_frames = (max(1, lima_cfg["number_of_frames"]) + self.min_size - 1) // self.min_size * self.min_size
            self.min_size = max(1, self.min_size)
            self.shape = (number_of_frames , lima_cfg.get("dimY", 1), lima_cfg.get("dimX", 1))
            self.chunk = (self.min_size, lima_cfg.get("dimY", 1), lima_cfg.get("dimX", 1))
            if "dtype" in lima_cfg:
                self.dtype = numpy.dtype(lima_cfg["dtype"])
            else:
                self.dtype = numpy.int32
            self.dataset = self.group.require_dataset(self.dataset_name, self.shape, dtype=self.dtype, chunks=self.chunk,
                                                      maxshape=(None,) + self.chunk[1:])
            self.dataset.attrs["interpretation"] = "image"
            self.dataset.attrs["metadata"] = self.metadata_grp
            self.dataset.attrs["signal"] = "1"
            self.group.parent["title"] = numpy.string_("Raw frames")
            self.group.parent["program"] = numpy.string_("LImA HDF5 plugin")
            self.group.parent["start_time"] = numpy.string_(getIsoTime())



    def flush(self):
        """
        Update some data like axis units and so on.
        
        @param radial: position in radial direction
        @param  azimuthal: position in azimuthal direction
        """
        logger.debug("HDF5 writer flush")
        with self._sem:
            if not self.hdf5:
                err = 'No opened file'
                logger.error(err)
                raise RuntimeError(err)
            if "stop_time" in self.group.parent:
                del  self.group.parent["stop_time"]
            self.group.parent["stop_time"] = numpy.string_(getIsoTime())
            self.hdf5.flush()

    def close(self):
        logger.debug("HDF5 writer close")
        self.flush()
        with self._sem:
            if self.hdf5:
                self.hdf5.close()
                self.hdf5 = None
                self.group = None
                self.dataset = None
                self.chunk = None
                self.size = None

    def write(self, data, index=0):
        """
        Minimalistic method to limit the overhead.
        """
        logger.debug("Write called on frame %s" % index)
        with self._sem:
            if self.dataset is None:
                logger.warning("Writer not initialized !")
                logger.info(self)
                return
            if index >= self.dataset.shape[0]:
                self.dataset.resize(index + 1, axis=0)
            self.dataset[index] = data

    def setConfig(self, config_dict=None):
        """
        Set the "static" configuration like filename and so on.
        
        @param config_dict: dict or JSON-serialized dict or file containing this dict.
        """
        logger.debug("HDF5 writer config with %s" % config_dict)
        if type(config_dict) in types.StringTypes:
            if os.path.isfile(config_dict):
                config = json.load(open(config_dict, "r"))
            else:
                 config = json.loads(config_dict)
        else:
            config = dict(config_dict)
        for k, v in  config.items():
            if k in self.CONFIG_ITEMS:
                self.__setattr__(k, v)

def test_basler():
    """
    """
    import argparse
    from Lima import Basler
    parser = argparse.ArgumentParser(description="Demo for HDF5 writer plugin",
                                     epilog="Author: Jérôme KIEFFER")
    parser.add_argument("-v", "--verbose",
                      action="store_true", dest="verbose", default=False,
                      help="switch to verbose/debug mode")
    parser.add_argument("-j", "--json",
                      dest="json", default=None,
                      help="json file containing the setup")
    parser.add_argument("-f", "--fps", type=float,
                      dest="fps", default="30",
                      help="Number of frames per seconds")
    parser.add_argument("-i", "--ip",
                      dest="ip", default="192.168.5.19",
                      help="IP address of the Basler camera")
    parser.add_argument("-n", "--nbframes", type=int,
                      dest="n", default=128,
                      help="number of frames to record")
    parser.add_argument('fname', nargs='*',
                   help='HDF5 filename ', default=["/tmp/lima_test.h5"])
#    parser.add_option("-l", "--lima",
#                      dest="lima", default=None,
#                      help="Base installation of LImA")
    options = parser.parse_args()
    if options.verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("Entering debug mode")
    cam = Basler.Camera(options.ip)
    iface = Basler.Interface(cam)
    ctrl = Core.CtControl(iface)
    extMgr = ctrl.externalOperation()
    myOp = extMgr.addOp(Core.USER_SINK_TASK, "HDF5writer", 10)
    writer = HDF5Writer(filename=options.fname[0])
    myTask = HDF5Sink(writer)
    myOp.setSinkTask(myTask)
    callback = StartAcqCallback(ctrl, myTask)
    myOp.registerCallback(callback)
    acq = ctrl.acquisition()
    acq.setAcqNbFrames(options.n)
    acq.setAcqExpoTime(1. / options.fps)
    ctrl.prepareAcq()
    ctrl.startAcq()
    while ctrl.getStatus().ImageCounters.LastImageReady < 1:
        time.sleep(0.5)
    logger.info("First frame arrived")
    time.sleep(1.0 * options.n / options.fps)
    logger.info("Waiting for last frame")
    while ctrl.getStatus().ImageCounters.LastImageReady < options.n - 1:
        time.sleep(0.5)
    logger.info("Closing HDF5 file")
    writer.close()

if __name__ == "__main__":
    test_basler()
