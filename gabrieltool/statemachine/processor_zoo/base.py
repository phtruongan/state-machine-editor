
# -*- coding: utf-8 -*-
"""Abstract base classes for processors
"""
import inspect
import json
import os
from functools import wraps

import cv2
import numpy as np
from cv2 import dnn
from logzero import logger
import copy
import sys

GABRIEL_DEBUG = os.getenv('GABRIEL_DEBUG', False)


faster_rcnn_root = os.getenv('FASTER_RCNN_ROOT','.')
sys.path.append(os.path.join(faster_rcnn_root, "tools"))
import _init_paths
from fast_rcnn.config import cfg as faster_rcnn_config
from fast_rcnn.test import im_detect
from fast_rcnn.nms_wrapper import nms
sys.path.append(os.path.join(faster_rcnn_root,"python"))

import caffe

# TODO (junjuew): move this to cvutil?


def drawPred(frame, class_name, conf, left, top, right, bottom):
    # Draw a bounding box.
    cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0))

    label = '%.2f' % conf

    label = '%s: %s' % (class_name, label)

    labelSize, baseLine = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    top = max(top, labelSize[1])
    cv2.rectangle(frame, (left, top - labelSize[1]), (left + labelSize[0], top + baseLine), (255, 255, 255), cv2.FILLED)
    cv2.putText(frame, label, (left, top), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0))
    return frame


def record_kwargs(func):
    """
    Automatically record constructor arguments

    >>> class process:
    ...     @record_kwargs
    ...     def __init__(self, cmd, reachable=False, user='root'):
    ...         pass
    >>> p = process('halt', True)
    >>> p.cmd, p.reachable, p.user
    ('halt', True, 'root')
    """
    names, varargs, keywords, defaults = inspect.getargspec(func)

    @wraps(func)
    def wrapper(self, *args, **kargs):
        kwargs = {}
        for name, default in zip(reversed(names), reversed(defaults)):
            kwargs[name] = default
        for name, arg in list(zip(names[1:], args)) + list(kargs.items()):
            kwargs[name] = arg
        setattr(self, 'kwargs', kwargs)
        func(self, *args, **kargs)

    return wrapper


class SerializableProcessor(object):
    def __init__(self, *args, **kwargs):
        super(SerializableProcessor, self).__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, json_obj):
        """Create a class instance from a json object.

        Subclasses should overide this class depending on the input type of
        their constructor.
        """
        return cls(**json_obj)

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.kwargs == other.kwargs
        return False

    def __ne__(self, other):
        return not self.__eq__(other)


class DummyProcessor(SerializableProcessor):

    @record_kwargs
    def __init__(self, dummy_input='dummy_input_value'):
        super(DummyProcessor, self).__init__()

    def __call__(self, image, debug=False):
        return {'dummy_key': 'dummy_value'}


class FasterRCNNOpenCVProcessor(SerializableProcessor):

    @record_kwargs
    def __init__(self, proto_path, model_path, labels=None, conf_threshold=0.8):
        # For default parameter settings,
        # see:
        # https://github.com/rbgirshick/fast-rcnn/blob/b612190f279da3c11dd8b1396dd5e72779f8e463/lib/fast_rcnn/config.py
        super(FasterRCNNOpenCVProcessor, self).__init__()
        self._scale = 600
        self._max_size = 1000
        # Pixel mean values (BGR order) as a (1, 1, 3) array
        # We use the same pixel mean for all networks even though it's not exactly what
        # they were trained with
        self._pixel_means = [102.9801, 115.9465, 122.7717]
        self._nms_threshold = 0.3
        self._labels = labels
        self._net = cv2.dnn.readNetFromCaffe(proto_path, model_path)
        self._conf_threshold = conf_threshold
        logger.debug(
            'Created a FasterRCNNOpenCVProcessor:\nDNN proto definition is at {}\n'
            'model weight is at {}\nlabels are {}\nconf_threshold is {}'.format(
                proto_path, model_path, self._labels, self._conf_threshold))

    @classmethod
    def from_json(cls, json_obj):
        try:
            kwargs = copy.copy(json_obj)
            kwargs['labels'] = json_obj['labels']
            kwargs['_conf_threshold'] = float(json_obj['conf_threshold'])
        except ValueError as e:
            raise ValueError(
                'Failed to convert json object to {} instance. '
                'The input json object is {}'.format(cls.__name__,
                                                     json_obj))
        return cls(**json_obj)

    def _getOutputsNames(self, net):
        #layersNames = net.getLayerNames()
        #return [layersNames[i[0] - 1] for i in net.getUnconnectedOutLayers()]
	return net.getUnconnectedOutLayersNames()
    def __call__(self, image):
        height, width = image.shape[:2]
	print "Start calling opencv"
        # resize image to correct size
        im_size_min = np.min(image.shape[0:2])
        im_size_max = np.max(image.shape[0:2])
        im_scale = float(self._scale) / float(im_size_min)
        # Prevent the biggest axis from being more than MAX_SIZE
        if np.round(im_scale * im_size_max) > self._max_size:
            im_scale = float(self._max_size) / float(im_size_max)
        im = cv2.resize(image, None, None, fx=im_scale, fy=im_scale,
                        interpolation=cv2.INTER_LINEAR)
        # create input data
        blob = cv2.dnn.blobFromImage(im, 1, (width, height), self._pixel_means,
                                     swapRB=False, crop=False)
        imInfo = np.array([height, width, im_scale], dtype=np.float32)
        self._net.setInput(blob, 'data')
        self._net.setInput(imInfo, 'im_info')

        # infer
	print self._getOutputsNames(self._net)
        outs = self._net.forward(self._getOutputsNames(self._net))
        t, _ = self._net.getPerfProfile()
        logger.debug('Inference time: %.2f ms' % (t * 1000.0 / cv2.getTickFrequency()))

        # postprocess
        layerNames = self._net.getLayerNames()
	lastLayerId = self._net.getLayerId(layerNames[-1])
	lastLayer = self._net.getLayer(lastLayerId)
	tmpResult = {}
	if lastLayer.type != 'DetectionOutput':
	    return tmpResult 
	classIds = []
        confidences = []
        boxes = []
        for out in outs:
            s = np.array(out)
	    print s.shape
	    for detection in out[0, 0]:
                confidence = detection[2]
                if confidence > self._conf_threshold:
                    left = int(detection[3])
                    top = int(detection[4])
                    right = int(detection[5])
                    bottom = int(detection[6])
                    width = right - left + 1
                    height = bottom - top + 1
                    classIds.append(int(detection[1]) - 1)  # Skip background label
                    confidences.append(float(confidence))
                    boxes.append([left, top, width, height])

        indices = cv2.dnn.NMSBoxes(boxes, confidences, self._conf_threshold, self._nms_threshold)
        results = {}
        for i in indices:
            i = i[0]
            box = boxes[i]
            left = box[0]
            top = box[1]
            width = box[2]
            height = box[3]
            classId = int(classIds[i])
            confidence = confidences[i]
            if self._labels[classId] not in results:
                results[self._labels[classId]] = []
            results[self._labels[classId]].append([left, top, left+width, top+height, confidence, classId])

        if GABRIEL_DEBUG:
            debug_image = image
            for (class_name, detections) in results.items():
                for detection in detections:
                    left, top, right, bottom, conf, _ = detection
                    debug_image = drawPred(debug_image, class_name, conf, left, top, right, bottom)
            cv2.imshow('debug', debug_image)
            cv2.waitKey(1)
        logger.debug('results: {}'.format(results))
        return results


class FasterRCNNProcessor(SerializableProcessor):

    @record_kwargs
    def __init__(self, proto_path, model_path, labels=None, conf_threshold=0.8):
        super(FasterRCNNProcessor, self).__init__()
        caffe.set_mode_gpu()
        caffe.set_device(0)
        faster_rcnn_config.GPU_ID = 0
	faster_rcnn_config.TEST.HAS_RPN = True
        self._scale = 600
        self._max_size = 640
        # Pixel mean values (BGR order) as a (1, 1, 3) array
        # We use the same pixel mean for all networks even though it's not exactly what
        # they were trained with
        self._pixel_means = [102.9801, 115.9465, 122.7717]
        self._nms_threshold = 0.3
        self._labels = labels
        self._net = caffe.Net(str(proto_path), str(model_path), caffe.TEST)
        self._conf_threshold = conf_threshold
        self._first = 1
	logger.debug(
            'Created a FasterRCNNProcessor:\nDNN proto definition is at {}\n'
            'model weight is at {}\nlabels are {}\nconf_threshold is {}'.format(
                proto_path, model_path, self._labels, self._conf_threshold))

    @classmethod
    def from_json(cls, json_obj):
        try:
            kwargs = copy.copy(json_obj)
            kwargs['labels'] = json_obj['labels']
            kwargs['_conf_threshold'] = float(json_obj['conf_threshold'])
        except ValueError as e:
            raise ValueError(
                'Failed to convert json object to {} instance. '
                'The input json object is {}'.format(cls.__name__,
                                                     json_obj))
        return cls(**json_obj)


    def __call__(self, image):
	if self._first == 1:
	    self._first = 0
	    caffe.set_mode_gpu()
	    caffe.set_device(0)
        height, width = image.shape[:2]
        ## preprocessing of input image
        resize_ratio = 1
        im = image
        if max(image.shape) > self._max_size:
            resize_ratio = float(self._max_size) / max(height, width)
            im = cv2.resize(image, (0,0), fx=resize_ratio, fy=resize_ratio, interpolation=cv2.INTER_AREA)

        # infer
	NMS_THRESH = self._nms_threshold
	CONF_THRESH = self._conf_threshold
    	scores, boxes = im_detect(self._net, im)
	results = {}
        for cls_idx in xrange(len(self._labels)):
            cls_idx += 1    # skip the background
            cls_boxes = boxes[:, 4 * cls_idx : 4 * (cls_idx + 1)]
            cls_scores = scores[:, cls_idx]

            # dets: detected results, each line is in [x1, y1, x2, y2, confidence] format
            dets = np.hstack((cls_boxes, cls_scores[:, np.newaxis])).astype(np.float32)

            # non maximum suppression
            keep = nms(dets, NMS_THRESH)
            dets = dets[keep, :]

            # filter out low confidence scores
            inds = np.where(dets[:, -1] >= CONF_THRESH)[0]
            dets = dets[inds, :]

            # now change dets format to [x1, y1, x2, y2, confidence, cls_idx]
            dets = np.hstack((dets, np.ones((dets.shape[0], 1)) * (cls_idx - 1)))
            for i in range(dets.shape[0]):
                classId = int(dets[i][5])
                if self._labels[classId] not in results:
                    results[self._labels[classId]] = []
                x1 = dets[i][0] / resize_ratio
                y1 = dets[i][1] / resize_ratio
                x2 = dets[i][2] / resize_ratio
                y2 = dets[i][3] / resize_ratio
                results[self._labels[classId]].append([x1, y1, x2, y2, dets[i][4], classId])

        logger.debug('results: {}'.format(results))
        return results

