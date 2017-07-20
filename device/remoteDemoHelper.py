import cv2
import numpy as np
import time
import threading
import queue
import itertools
import requests
import base64
import json
import os
from azure.storage.queue import QueueService

# Class to hold info about the model that the app needs to call the model and display result correctly

class ModelHelper:
    def __init__(self, argv, modelName, modelFiles, labelsFile, inputHeightAndWidth=(224, 224), scaleFactor=1 / 255, threshold=0.25):
        """ Helper class to store information about the model we want to use.
        argv       - arguments passed in from the command line
        modelName  - string name of the model
        modelFiles - list of strings containing darknet .cfg filename and darknet .weights filename, or CNTK model file name.
        labelsFile - string name of labels that correspond to the predictions output of the model
        inputHeightAndWidth - a list of two values giving the rows and columns of the input image for the model e.g. (224, 224)
        scaleFactor - each input pixel may need to be scaled. It is common for models to require an 8-bit pixel
                      to be represented as a value between 0.0 and 1.0, which is the same as multiplying it by 1/255.
        threshold   - specifies a prediction threshold. We will ignore prediction values less than this
        """
        self.model_name = modelName
        self.model_files = modelFiles
        self.labels_file = labelsFile
        self.inputHeightAndWidth = inputHeightAndWidth
        self.scaleFactor = scaleFactor
        self.threshold = threshold
        self.labels = self.load_labels(self.labels_file)
        self.start = time.time()
        self.frame_count = 0
        self.fps = 0
        self.camera = None
        self.imageFilenames = None
        self.captureThread = None
        self.save_images = None
        self.bingKey = os.environ.get('BING_IMAGE_SEARCH_KEY')
        self.storageKey = os.environ.get('STORAGE_KEY')
        self.computerVisionApiKey = os.environ.get('CV_API_KEY')

        # now parse the arguments
        self.parse_arguments(argv)
    
    def parse_arguments(self, argv):
        # Parse arguments
        self.camera = 0
        self.imageFilenames = []
        for i in range(1,len(argv)):            
            arg1 = argv[i]
            if (arg1 == "-save"):
                self.save_images = 1
            elif arg1.isdigit():
                self.camera = int(arg1) 
            else:
                self.imageFilenames.append(arg1)
                self.camera = None

    def show_image(self, frameToShow):          
        cv2.imshow('frame', frameToShow)
        if (not self.save_images is None):
            name = 'frame' + str(self.save_images) + ".png"
            cv2.imwrite(name, frameToShow)
            self.save_images = self.save_images + 1

    def load_labels(self, fileName):
        labels = []
        with open(fileName) as f:
            labels = f.read().splitlines()
        return labels

    def get_top_n(self, predictions, N):
        """Return at most the top 5 predictions as a list of tuples that meet the threashold."""
        topN = np.zeros([N, 2])
        for p in range(len(predictions)):
            for t in range(len(topN)):
                if predictions[p] > topN[t][0]:
                    topN[t] = [predictions[p], p]
                    break
        result = []
        for element in topN:
            if (element[0] > self.threshold):
                result.append(
                    (self.labels[int(element[1])], round(element[0], 2)))
        return result

    def get_predictor_map(self, predictor, intervalMs):
        """Creates an ELL map from an ELL predictor"""
        import ell_utilities

        name = self.model_name
        if (intervalMs > 0):
            ell_map = ell_utilities.ell_steppable_map_from_float_predictor(
                predictor, intervalMs, name + "InputCallback", name + "OutputCallback")
        else:
            ell_map = ell_utilities.ell_map_from_float_predictor(predictor)
        return ell_map
        
    def save_ell_predictor_to_file(self, predictor, filePath, intervalMs=0):
        """Saves an ELL predictor to file so that it can be compiled to run on a device, with an optional stepInterval in milliseconds"""
        ell_map = self.get_predictor_map(predictor, intervalMs)
        ell_map.Save(filePath)

    def init_image_source(self):
        # Start video capture device or load static image
        if True:
            stream = AzureQueueStream("dog", self.bingKey, self.computerVisionApiKey, self.storageKey)
        elif self.camera is not None:
            stream = FrameStream(VideoCaptureSource(self.camera), clear=True)
        elif self.imageFilenames:
            stream = FrameStream(FileCaptureSource(self.imageFilenames))
        self.captureThread = stream.start()

    def get_next_frame(self):
        frameInfo = self.captureThread.next_frame()
        return (frameInfo.get("frame"), frameInfo);
        
    def resize_image(self, image, newSize):
        # Shape: [rows, cols, channels]
        """Crops, resizes image to outputshape. Returns image as numpy array in in RGB order, with each pixel multiplied by the configured scaleFactor."""
        if (image.shape[0] > image.shape[1]): # Tall (more rows than cols)
            rowStart = int((image.shape[0] - image.shape[1]) / 2)
            rowEnd = rowStart + image.shape[1]
            colStart = 0
            colEnd = image.shape[1]
        else: # Wide (more cols than rows)
            rowStart = 0
            rowEnd = image.shape[0]
            colStart = int((image.shape[1] - image.shape[0]) / 2)
            colEnd = colStart + image.shape[0]

        cropped = image[rowStart:rowEnd, colStart:colEnd]
        resized = cv2.resize(cropped, newSize)
        return resized
    
    def prepare_image_for_predictor(self, image):
        """Crops, resizes image to outputshape. Returns image as numpy array in in RGB order, with each pixel multiplied by the configured scaleFactor."""
        resized = self.resize_image(image, self.inputHeightAndWidth)
        resized = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        resized = resized * self.scaleFactor
        resized = resized.astype(np.float).ravel()
        return resized

    def draw_label(self, image, label):
        """Helper to draw text onto an image"""
        cv2.rectangle(
            image, (0, 0), (image.shape[1], 40), (50, 200, 50), cv2.FILLED)
        cv2.putText(image, label, (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)
        return

    def draw_fps(self, image):
        now = time.time()
        if (self.frame_count > 0):
            diff = now - self.start
            if (diff >= 1):
                self.fps = round(self.frame_count / diff, 1)
                self.frame_count = 0
                self.start = now

        label = "fps " + str(self.fps)
        labelSize, baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
        width = image.shape[1]
        height = image.shape[0]
        pos = (width - labelSize[0] - 5, labelSize[1]+5)
        cv2.putText(image, label, pos, cv2.FONT_HERSHEY_SIMPLEX,
                    0.4, (0, 0, 128), 1, cv2.LINE_AA)
        self.frame_count = self.frame_count + 1
    
    def get_wait(self):
        speed = self.fps
        if (speed == 0): 
            speed = 1
        if (speed > 1):
            return 1
        return 3

    def done(self):        
        # on slow devices this helps let the images to show up on screen
        result = False
        for i in range(self.get_wait()):
            if cv2.waitKey(1) & 0xFF == 27:
                result = True
                break
        return result

    def send_to_azure(self, frameInfo, text):
        self.captureThread.send_to_azure(frameInfo, text)

class VideoCaptureSource:
    def __init__(self, path):
        self.stream = cv2.VideoCapture(path)

    def get_image(self):
        (grabbed, frame) = self.stream.read()
        return (grabbed, frame)

class FileCaptureSource:
    def __init__(self, path):
        self.images = itertools.cycle(path)

    def get_image(self):
        imageFile = next(self.images)
        frame = cv2.imread(imageFile)
        if (type(frame) == type(None)):
            raise Exception('image from %s failed to load' % (imageFile))
        return (1, frame, {'file': imageFile})

class BingImageSource:
    def __init__(self, searchTerm, bingKey, computerVisionApiKey, messageTemplate=None):
        self.searchTerm = searchTerm
        self.bingKey = bingKey
        self.computerVisionApiKey = computerVisionApiKey
        self.offset = 0
        self.queue = queue.Queue()
        self.counter = 0
        self.maxResultsPerPage = 10
        self.messageTemplate = messageTemplate

    def get_image(self):
        if (self.queue.empty()):
            self.fetch_images()

        if (not self.queue.empty()):
            imageUrl = self.queue.get()
            image = requests.get(imageUrl)
            image2 = np.asarray(bytearray(image.content), dtype="uint8")
            frame = cv2.imdecode(image2, cv2.IMREAD_COLOR)
            frameInfo = {'url': imageUrl, 'template': self.messageTemplate}
            self.counter += 1
            if self.counter <= 10 and self.computerVisionApiKey:
                url = 'https://westeurope.api.cognitive.microsoft.com/vision/v1.0/describe?maxCandidates=1'
                payload = {'url': imageUrl}
                headers = {
                    'Ocp-Apim-Subscription-Key': self.computerVisionApiKey,
                    'Content-Type': 'application/json'
                }
                r = requests.post(url, json=payload, headers=headers)
                r.raise_for_status()
                captions = r.json().get("description", {}).get("captions", [])
                if (captions):
                    frameInfo['visionApiLabel'] = captions[0].get("text", "")
                    frameInfo['visionApiConfidence'] = captions[0].get("confidence", "")
            return (1, frame, frameInfo)

        print("no more results")
        return (0, None)

    def fetch_images(self):
        url = 'https://api.cognitive.microsoft.com/bing/v5.0/images/search'
        payload = {'q': self.searchTerm, 'count': self.maxResultsPerPage, 'offset': self.offset, 'mkt': 'en-us', 'safeSearch': 'Strict'}
        headers = {'Ocp-Apim-Subscription-Key': self.bingKey }
        r = requests.get(url, params=payload, headers=headers)
        r.raise_for_status()
        self.offset = self.offset + r.json().get('nextOffsetAddCount', 1)
        for result in r.json().get('value', []):
            imageUrl = result['thumbnailUrl']
            self.queue.put(imageUrl)


class FrameStream:
    def __init__(self, source, queueSize=128, clear=False):
        self.stream = source
        self.stop_event = threading.Event()
        self.clear = clear
        self.frameQueue = queue.Queue(maxsize=queueSize)

    def start(self):
        t = threading.Thread(target=self.read_frames_from_source)
        t.daemon = True
        t.start()
        return self
    
    def read_frames_from_source(self):
        while True:
            if self.stop_event.is_set():
                return
 
            if not self.frameQueue.full():
                (grabbed, frame, attrs) = self.stream.get_image()
 
                if not grabbed:
                    self.stop()
                    return

                while (self.clear and not self.frameQueue.empty()):
                    self.frameQueue.get()
 
                self.frameQueue.put({'frame': frame, 'attrs': attrs})

    def next_frame(self):
        return self.frameQueue.get()

    def stop(self):
        self.stop_event.set()

class AzureQueueStream:
    def __init__(self, start_topic, bingKey, computerVisionApiKey, storageKey):
        self.stop_event = threading.Event()
        self.counter = 0
        self.bingKey = bingKey
        self.computerVisionApiKey = computerVisionApiKey

        self.queue_service = None
        if storageKey:
            self.queue_service = QueueService(account_name='rpiimagedetectj34n5m', account_key=storageKey)
            self.queue_service.create_queue('rpi-queue')
            self.queue_service.put_message('rpi-queue', base64.b64encode(b'{"Text":"grizzly"}').decode('ascii'))
        self.bing_thread = FrameStream(BingImageSource(start_topic, self.bingKey, self.computerVisionApiKey)).start()

    def start(self):
        t = threading.Thread(target=self.get_topic)
        t.daemon = True
        t.start()
        return self
    
    def get_topic(self):
        while True:
            if self.stop_event.is_set():
                return

            # drain queue, retrieve last message
            lastMsg = None

            if self.queue_service:
                messages = self.queue_service.get_messages('rpi-queue')
                for message in messages:
                    lastMsg = message
                    self.queue_service.delete_message('rpi-queue', message.id, message.pop_receipt)        

            if lastMsg and lastMsg.content:
                print("content",  lastMsg.content)
                jsonMsg = json.loads(base64.b64decode(lastMsg.content).decode('ascii'))
                searchTopic = jsonMsg.get("Text", "dog")
                print("Bing Image Search topic:", searchTopic)
                self.bing_thread.stop()
                self.bing_thread = FrameStream(BingImageSource(searchTopic, self.bingKey, self.computerVisionApiKey, jsonMsg), queueSize = 16).start()
                self.counter = 0
            time.sleep(2)
 
    def next_frame(self):
        return self.bing_thread.next_frame()

    def stop(self):
        self.bing_thread.stop()
        self.stop_event.set()

    def send_to_azure(self, frameInfo, predictions):
        msg = frameInfo.get("attrs", {}).get("template")
        if self.queue_service and self.counter < 10 and msg and msg.get("RelatesTo", {}):
            self.counter += 1
            msg["Url"] = frameInfo.get("attrs", {}).get("url", "")
            msg["Text"] = None
            if predictions:
                msg["Text"] = predictions[0][0]
            msg["Label"] = frameInfo.get("attrs", {}).get("visionApiLabel", "")
            self.queue_service.put_message('bot-queue', base64.b64encode(json.dumps(msg).encode('ascii')).decode('ascii'))
