#!/usr/bin/env python
# -*- coding: utf-8 -*-
# File: dataReader.py
# Author: Amir Alansary <amiralansary@gmail.com>

import warnings
warnings.simplefilter("ignore", category=ResourceWarning)
import pandas as pd
import numpy as np
import SimpleITK as sitk
import itk
from tensorpack import logger
from IPython.core.debugger import set_trace

__all__ = ['filesListBrainMRLandmark','NiftiImage']

######################################################################
## extract points from txt file
def getLandmarksFromTXTFile(file):
    '''
    Extract each landmark point line by line and return vector continaing all landmarks.
    '''
    with open(file) as fp:
        landmarks = []
        for i, line in enumerate(fp):
            landmarks.append([float(k) for k in line.split(',')])
        landmarks = np.asarray(landmarks).reshape((-1,3))
        return landmarks

def getLandmarksFromFCSVFile(file):
    df = pd.read_csv(
        file,
        header=None,
        comment="#",
        names=["fiducial_labels", "xcoord", "ycoord", "zcoord", "sel", "vis"],
    )
    # the sign flipping in x and y is to convert RAS(used by slicer) to LPS(used in DICOM and itk)
    xcoord = -1 * df["xcoord"].values.reshape(-1, 1)
    ycoord = -1 * df["ycoord"].values.reshape(-1, 1)
    zcoord = df["zcoord"].values.reshape(-1, 1)
    vec = np.concatenate((xcoord, ycoord, zcoord), axis=1).reshape(-1, 3)
    return vec

###############################################################################

class filesListBrainMRLandmark(object):
    """ A class for managing train files for mri cardio data

        Attributes:
        files_list: Two or on textfiles that contain a list of all images and (landmarks)
        returnLandmarks: Return landmarks if task is train or eval (default: True)
    """
    def __init__(self, files_list=None, returnLandmarks=True, fiducial=0):
        # check if files_list exists
        assert files_list, 'There is no directory containing files list'
        # read image filenames
        self.image_files = [line.split('\n')[0] for line in open(files_list[0].name)]
        # read landmark filenames if task is train or eval
        self.returnLandmarks = returnLandmarks
        self.fiducial = fiducial
        if self.returnLandmarks:
            self.landmark_files = [line.split('\n')[0] for line in open(files_list[1].name)]
            assert len(self.image_files)== len(self.landmark_files), 'number of image files is not equal to number of landmark files'


    @property
    def num_files(self):
        return len(self.image_files)

    def sample_circular(self,shuffle=False):
        """ return a random sampled ImageRecord from the list of files
        """
        if shuffle:
            indexes = rng.choice(x,len(x),replace=False)
        else:
            indexes = np.arange(self.num_files)

        while True:
            for idx in indexes:
                sitk_image, image = NiftiImage().decode(self.image_files[idx])
                if self.returnLandmarks:
                    ## transform landmarks to image space if they are in physical space
                    landmark_file = self.landmark_files[idx]
                    if ".fcsv" in landmark_file:
                        all_landmarks = getLandmarksFromFCSVFile(landmark_file)
                    else:
                        all_landmarks = getLandmarksFromTXTFile(landmark_file)
                    landmark = all_landmarks[self.fiducial] # landmark index is 13 for ac-point and 14 pc-point
                    # transform landmark from physical to image space if required
                    if ".fcsv" in landmark_file:
                        landmark = sitk_image.TransformPhysicalPointToContinuousIndex(landmark)
                    landmark = np.round(landmark).astype('int')
                else:
                    landmark = None
                # extract filename from path
                image_filename = self.image_files[idx]
                yield image, landmark, image_filename, sitk_image.GetSpacing()

###############################################################################

class ImageRecord(object):
  '''image object to contain height,width, depth and name '''
  pass


class NiftiImage(object):
    """Helper class that provides TensorFlow image coding utilities."""
    def __init__(self):
        pass

    def _is_nifti(self,filename):
        """Determine if a file contains a nifti format image.
        Args
          filename: string, path of the image file
        Returns
          boolean indicating if the image is a nifti
        """
        extensions = ['.nii','.nii.gz','.img','.hdr']
        return any(i in filename for i in extensions)

    def decode(self, filename,label=False):
        """ decode a single nifti image
        Args
          filename: string for input images
          label: True if nifti image is label
        Returns
          image: an image container with attributes; name, data, dims
        """
        image = ImageRecord()
        image.name = filename
        assert self._is_nifti(image.name), "unknown image format for %r" % image.name

        if label:
            sitk_image = sitk.ReadImage(image.name, sitk.sitkInt8)
        else:
            sitk_image = sitk.ReadImage(image.name, sitk.sitkFloat32)
            np_image = sitk.GetArrayFromImage(sitk_image)
            itk_image = itk.imread(image.name)
            region = itk_image.GetLargestPossibleRegion()
            index = np.array(region.GetIndex())
            size = np.array(region.GetSize())
            center = index + size / 2
            center = sitk_image.TransformContinuousIndexToPhysicalPoint(center)
            spacing = np.array(sitk_image.GetSpacing())
            rif = sitk.ResampleImageFilter()
            identity_transform = sitk.Transform(3, sitk.sitkIdentity)
            rif.SetOutputSpacing(sitk_image.GetSpacing())
            rif.SetTransform(identity_transform)
            rif.SetOutputOrigin(tuple(np.array(center)) - spacing * size / 2)
            rif.SetOutputDirection([1, 0, 0, 0, 1, 0, 0, 0, 1])
            rif.SetSize(sitk_image.GetSize())
            sitk_image = rif.Execute(sitk_image)
            # threshold image between p10 and p98 then re-scale [0-255]
            p0 = np_image.min().astype('float')
            p10 = np.percentile(np_image,10)
            p99 = np.percentile(np_image,99)
            p100 = np_image.max().astype('float')
            # logger.info('p0 {} , p5 {} , p10 {} , p90 {} , p98 {} , p100 {}'.format(p0,p5,p10,p90,p98,p100))
            sitk_image = sitk.Threshold(sitk_image,
                                        lower=p10,
                                        upper=p100,
                                        outsideValue=p10)
            sitk_image = sitk.Threshold(sitk_image,
                                        lower=p0,
                                        upper=p99,
                                        outsideValue=p99)
            sitk_image = sitk.RescaleIntensity(sitk_image,
                                               outputMinimum=0,
                                               outputMaximum=255)

        # Convert from [depth, width, height] to [width, height, depth]
        image.data = sitk.GetArrayFromImage(sitk_image).transpose(2,1,0)#.astype('uint8')
        image.dims = np.shape(image.data)

        return sitk_image, image
