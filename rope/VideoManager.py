import os
import cv2
import io
import imageio
import tkinter as tk
from PIL import Image, ImageTk
import threading
import time
import numpy as np
import numexpr as ne
import cupy as cp
from skimage import transform as trans
import subprocess
from math import floor, ceil
import bisect
import onnxruntime
import torch.utils
import torchvision
from torchvision.transforms.functional import normalize #update to v2
import torch
from torchvision import transforms
torchvision.disable_beta_transforms_warning()
from torchvision.transforms import v2
torch.set_grad_enabled(False)
onnxruntime.set_default_logger_severity(4)
import rope.FaceUtil as faceutil

import inspect #print(inspect.currentframe().f_back.f_code.co_name, 'resize_image')
import pyvirtualcam
import platform
import psutil
from dfl.DFMModel import DFMModel

device = 'cuda'

lock=threading.Lock()

class VideoManager():
    def __init__(self, models ):
        self.virtcam = False
        self.models = models
        # Model related
        self.input_names = []               # names of the inswapper.onnx inputs
        self.input_size = []                # size of the inswapper.onnx inputs

        self.output_names = []              # names of the inswapper.onnx outputs
        self.arcface_dst = np.array( [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

        self.video_file = []

        self.FFHQ_kps = np.array([[ 192.98138, 239.94708 ], [ 318.90277, 240.1936 ], [ 256.63416, 314.01935 ], [ 201.26117, 371.41043 ], [ 313.08905, 371.15118 ] ])

        #Video related
        self.capture = []                   # cv2 video
        self.is_video_loaded = False        # flag for video loaded state
        self.video_frame_total = None       # length of currently loaded video
        self.play = False                   # flag for the play button toggle
        self.current_frame = 0              # the current frame of the video
        self.create_video = False
        self.output_video = []
        self.file_name = []

        # Play related
        # self.set_read_threads = []          # Name of threaded function
        self.frame_timer = 0.0      # used to set the framerate during playing

        # Queues
        self.action_q = []                  # queue for sending to the coordinator
        self.frame_q = []                   # queue for frames that are ready for coordinator

        self.r_frame_q = []                 # queue for frames that are requested by the GUI
        self.read_video_frame_q = []

        # swapping related
        # self.source_embedding = []          # array with indexed source embeddings

        self.found_faces = []   # array that maps the found faces to source faces

        self.parameters = []

        # Face Editor
        self.parameters_face_editor = []

        self.target_video = []

        self.fps = 1.0
        self.temp_file = []

        self.clip_session = []

        self.start_time = []
        self.record = False
        self.output = []
        self.image = []

        self.saved_video_path = []
        self.sp = []
        self.timer = []
        self.fps_average = []
        self.total_thread_time = 0.0

        self.start_play_time = []
        self.start_play_frame = []

        self.rec_thread = []
        self.markers = []
        self.is_image_loaded = False
        self.stop_marker = -1
        self.perf_test = False

        self.control = []

        self.process_q =    {
                            "Thread":                   [],
                            "FrameNumber":              [],
                            "ProcessedFrame":           [],
                            "Status":                   'clear',
                            "ThreadTime":               []
                            }
        self.process_qs = []
        self.rec_q =    {
                            "Thread":                   [],
                            "FrameNumber":              [],
                            "Status":                   'clear'
                            }
        self.rec_qs = []

        # Face Landmarks
        self.face_landmarks = []

        # Face Editor
        self.face_editor = []

    def assign_found_faces(self, found_faces):
        self.found_faces = found_faces

    def enable_virtualcam(self):
        #Check if capture contains any cv2 stream or is it an empty list
        if not isinstance(self.capture, (list)):
            vid_height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            vid_width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.disable_virtualcam()
            try:
                self.virtcam = pyvirtualcam.Camera(width=vid_width, height=vid_height, fps=self.fps)
            except Exception as e:
                print(e)
    def disable_virtualcam(self):
        if self.virtcam:
            self.virtcam.close()
        self.virtcam = False
        # print("Disable hello")
    def webcam_selected(self, file):
        return ('Webcam' in file) and len(file)==8

    def change_webcam_resolution_and_fps(self):
        if self.video_file:
            if self.webcam_selected(self.video_file):
                if self.play:
                    self.play_video('stop')
                    time.sleep(1)
                self.load_target_video(self.video_file)
                self.add_action('clear_faces_stop_swap', None)
                self.add_action('clear_stop_enhance', None)
                self.add_action('clear_stop_faces_editor', None)

    def load_target_video( self, file ):
        # If we already have a video loaded, release it
        if self.capture:
            self.capture.release()

        if self.control['VirtualCameraSwitch']:
            self.add_action("set_virtual_cam_toggle_disable",None)
            self.disable_virtualcam()

        # Open file
        self.video_file = file
        if self.webcam_selected(file):
            webcam_index = int(file[-1])
            # Only use dshow if it is a Physical webcam in Windows
            if platform.system == 'Windows':
                try:
                    self.capture = cv2.VideoCapture(webcam_index, cv2.CAP_DSHOW)
                except:
                    self.capture = cv2.VideoCapture(webcam_index)
            else:
                self.capture = cv2.VideoCapture(webcam_index)

            res_width, res_height = self.parameters['WebCamMaxResolSel'].split('x')
            self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(res_width))
            self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(res_height))
            self.fps = self.parameters['WebCamMaxFPSSel']

        else:
            self.capture = cv2.VideoCapture(file)
            self.fps = self.capture.get(cv2.CAP_PROP_FPS)

        if not self.capture.isOpened():
            if self.webcam_selected(file):
                print("Cannot open file: ", file)

        else:
            self.target_video = file
            self.is_video_loaded = True
            self.is_image_loaded = False
            if not self.webcam_selected(file):
                self.video_frame_total = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
            else:
                self.video_frame_total = 99999999
            self.play = False
            self.current_frame = 0
            self.frame_timer = time.time()
            self.frame_q = []
            self.r_frame_q = []
            self.found_faces = []
            self.add_action("set_slider_length",self.video_frame_total-1)
            self.add_action("set_slider_fps",self.fps)

        self.capture.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame)
        success, image = self.capture.read()

        if success:
            crop = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)  # RGB
            temp = [crop, False]
            self.r_frame_q.append(temp)
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame)

            # Face Landmarks
            if self.face_landmarks:
                self.face_landmarks.remove_all_data()
                self.face_landmarks.apply_changes_to_widget_and_parameters(self.current_frame, 1)

            # Face Editor
            if self.face_editor:
                self.face_editor.remove_all_data()
                self.face_editor.apply_changes_to_widget_and_parameters(self.current_frame, 1)

            self.add_action("clear_stop_enhance", None)

    def load_target_image(self, file):
        if self.capture:
            self.capture.release()
        self.is_video_loaded = False
        self.play = False
        self.frame_q = []
        self.r_frame_q = []
        self.found_faces = []
        self.image = cv2.imread(file) # BGR
        self.image = cv2.cvtColor(self.image, cv2.COLOR_BGR2RGB) # RGB
        temp = [self.image, False]
        self.frame_q.append(temp)

        # Face Landmarks
        if self.face_landmarks:
            self.face_landmarks.remove_all_data()
            self.face_landmarks.apply_changes_to_widget_and_parameters(self.current_frame, 1)

        # Face Editor
        if self.face_editor:
            self.face_editor.remove_all_data()
            self.face_editor.apply_changes_to_widget_and_parameters(self.current_frame, 1)

        self.add_action("clear_stop_enhance", None)

        self.is_image_loaded = True

    ## Action queue
    def add_action(self, action, param):
        # print(inspect.currentframe().f_back.f_code.co_name, '->add_action: '+action)
        temp = [action, param]
        self.action_q.append(temp)

    def get_action_length(self):
        return len(self.action_q)

    def get_action(self):
        action = self.action_q[0]
        self.action_q.pop(0)
        return action

    ## Queues for the Coordinator
    def get_frame(self):
        frame = self.frame_q[0]
        self.frame_q.pop(0)
        return frame

    def get_frame_length(self):
        return len(self.frame_q)

    def get_requested_frame(self):
        frame = self.r_frame_q[0]
        self.r_frame_q.pop(0)
        return frame

    def get_requested_frame_length(self):
        return len(self.r_frame_q)

    def get_requested_video_frame(self, frame, marker=True):
        temp = []
        if self.is_video_loaded:

            if self.play == True:
                self.play_video("stop")
                self.process_qs = []

            # Face Landmarks
            apply_landmarks = (self.current_frame != int(frame))
            #
            self.current_frame = int(frame)

            self.capture.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame)
            success, target_image = self.capture.read() #BGR

            if success:
                # Face Landmarks
                if self.parameters['LandmarksPositionAdjSwitch'] and apply_landmarks and self.face_landmarks:
                    self.face_landmarks.apply_changes_to_widget_and_parameters(self.current_frame, 1)

                # Face Editor
                if apply_landmarks and self.face_editor:
                    self.face_editor.apply_changes_to_widget_and_parameters(self.current_frame, 1)

                target_image = cv2.cvtColor(target_image, cv2.COLOR_BGR2RGB) #RGB
                if not self.control['SwapFacesButton'] and not self.control['EditFacesButton']:
                    temp = [target_image, self.current_frame] #temp = RGB
                else:
                    temp = [self.swap_video(target_image, self.current_frame, marker), self.current_frame] # temp = RGB

                if self.control['EnhanceFrameButton']:
                    temp[0] = self.enhance_video(temp[0], self.current_frame, marker) # temp = RGB

                self.r_frame_q.append(temp)
        elif self.is_image_loaded:
            if not self.control['SwapFacesButton'] and not self.control['EditFacesButton']:
                temp = [self.image, self.current_frame] # image = RGB

            else:
                temp = [self.swap_video(self.image, self.current_frame, False), self.current_frame] # image = RGB

            if self.control['EnhanceFrameButton']:
                temp[0] = self.enhance_video(temp[0], self.current_frame, False) # image = RGB

            self.r_frame_q.append(temp)

    def find_lowest_frame(self, queues):
        min_frame=999999999
        index=-1

        for idx, thread in enumerate(queues):
            frame = thread['FrameNumber']
            if frame != []:
                if frame < min_frame:
                    min_frame = frame
                    index=idx
        return index, min_frame

    def play_video(self, command):
        # print(inspect.currentframe().f_back.f_code.co_name, '->play_video: ')
        if command == "play":
            # Initialization
            self.play = True
            self.fps_average = []
            self.process_qs = []
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame)
            self.frame_timer = time.time()

            # Create reusable queue based on number of threads
            for i in range(self.parameters['ThreadsSlider']):
                    new_process_q = self.process_q.copy()
                    self.process_qs.append(new_process_q)

            # Start up audio if requested
            if self.control['AudioButton']:
                seek_time = (self.current_frame)/self.fps
                args =  ["ffplay",
                        '-vn',
                        '-ss', str(seek_time),
                        '-nodisp',
                        '-stats',
                        '-loglevel',  'quiet',
                        '-sync',  'audio',
                        '-af', f'atempo={self.parameters["AudioSpeedSlider"]}',
                        self.video_file]

                self.audio_sp = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

                # Parse the console to find where the audio started
                while True:
                    temp = self.audio_sp.stdout.read(69)
                    if temp[:7] != b'    nan':
                        try:
                            sought_time = float(temp[:7].strip())
                            self.current_frame = int(self.fps*sought_time)
                            self.capture.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame)
                        except Exception as e:
                            #print(e)
                            pass
                        break

#'    nan    :  0.000
#'   1.25 M-A:  0.000 fd=   0 aq=   12KB vq=    0KB sq=    0B f=0/0'

        elif command == "stop":
            self.play = False
            self.add_action("stop_play", True)

            index, min_frame = self.find_lowest_frame(self.process_qs)

            if index != -1:
                self.current_frame = min_frame-1

            self.terminate_audio_process_tree()

            torch.cuda.empty_cache()

        elif command=='stop_from_gui':
            self.play = False

            # Find the lowest frame in the current render queue and set the current frame to the one before it
            index, min_frame = self.find_lowest_frame(self.process_qs)
            if index != -1:
                self.current_frame = min_frame-1

            self.terminate_audio_process_tree()

            torch.cuda.empty_cache()

        elif command == "record":
            self.record = True
            self.play = True
            self.total_thread_time = 0.0
            self.process_qs = []
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame)

            for i in range(self.parameters['ThreadsSlider']):
                    new_process_q = self.process_q.copy()
                    self.process_qs.append(new_process_q)

           # Initialize
            self.timer = time.time()
            frame_width = int(self.capture.get(3))
            frame_height = int(self.capture.get(4))

            self.start_time = float(self.capture.get(cv2.CAP_PROP_POS_FRAMES) / float(self.fps))

            self.file_name = os.path.splitext(os.path.basename(self.target_video))
            base_filename =  self.file_name[0]+"_"+str(time.time())[:10]
            self.output = os.path.join(self.saved_video_path, base_filename)
            self.temp_file = self.output+"_temp"+self.file_name[1]

            if self.parameters['RecordTypeTextSel']=='FFMPEG':
                args =  ["ffmpeg",
                        '-hide_banner',
                        '-loglevel',    'error',
                        "-an",
                        "-r",           str(self.fps),
                        "-i",           "pipe:",
                        # '-g',           '25',
                        "-vf",          "format=yuvj420p",
                        "-c:v",         "libx264",
                        "-crf",         str(self.parameters['VideoQualSlider']),
                        "-r",           str(self.fps),
                        "-s",           str(frame_width)+"x"+str(frame_height),
                        self.temp_file]

                self.sp = subprocess.Popen(args, stdin=subprocess.PIPE)

            elif self.parameters['RecordTypeTextSel']=='OPENCV':
                size = (frame_width, frame_height)
                self.sp = cv2.VideoWriter(self.temp_file,  cv2.VideoWriter_fourcc(*'mp4v') , self.fps, size)

    def terminate_audio_process_tree(self):
        if hasattr(self, 'audio_sp') and self.audio_sp is not None:
            parent_pid = self.audio_sp.pid

            try:
                # Terminate any child processes spawned by ffplay
                try:
                    parent_proc = psutil.Process(parent_pid)
                    children = parent_proc.children(recursive=True)
                    for child in children:
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass  # The child process has already terminated
                except psutil.NoSuchProcess:
                    pass  # The parent process has already terminated

                # Terminate the parent process
                self.audio_sp.terminate()
                try:
                    self.audio_sp.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.audio_sp.kill()

            except psutil.NoSuchProcess:
                pass  # The process no longer exists

            self.audio_sp = None

    # @profile
    def process(self):
        process_qs_len = range(len(self.process_qs))

        # Add threads to Queue
        if self.play == True and self.is_video_loaded == True:
            for item in self.process_qs:
                if item['Status'] == 'clear' and self.current_frame < self.video_frame_total:
                    item['Thread'] = threading.Thread(target=self.thread_video_read, args = [self.current_frame]).start()
                    item['FrameNumber'] = self.current_frame
                    item['Status'] = 'started'
                    item['ThreadTime'] = time.time()

                    self.current_frame += 1
                    break

        else:
            self.play = False

        # Always be emptying the queues
        time_diff = time.time() - self.frame_timer

        if not self.record and time_diff >= 1.0/float(self.fps) and self.play:

            index, min_frame = self.find_lowest_frame(self.process_qs)

            if index != -1:
                if self.process_qs[index]['Status'] == 'finished':
                    temp = [self.process_qs[index]['ProcessedFrame'], self.process_qs[index]['FrameNumber']]
                    self.frame_q.append(temp)

                    # Report fps, other data
                    self.fps_average.append(1.0/time_diff)
                    avg_fps = self.fps / self.fps_average[-1] if self.fps_average else 10

                    # self.send_to_virtual_camera(temp[0], 15)
                    if self.control['VirtualCameraSwitch'] and self.virtcam:
                        # print("virtcam",self.virtcam)
                        try:
                            self.virtcam.send(temp[0])
                            self.virtcam.sleep_until_next_frame()
                        except Exception as e:
                            print(e)
                    if len(self.fps_average) >= floor(self.fps):
                        fps = round(np.average(self.fps_average), 2)
                        msg = "%s fps, %s process time" % (fps, round(self.process_qs[index]['ThreadTime'], 4))
                        self.fps_average = []

                    if self.process_qs[index]['FrameNumber'] >= self.video_frame_total-1 or self.process_qs[index]['FrameNumber'] == self.stop_marker:
                        self.play_video('stop')

                    self.process_qs[index]['Status'] = 'clear'
                    self.process_qs[index]['Thread'] = []
                    self.process_qs[index]['FrameNumber'] = []
                    self.process_qs[index]['ThreadTime'] = []
                    self.frame_timer += 1.0/self.fps

        if not self.webcam_selected(self.video_file):
            if self.record:

                index, min_frame = self.find_lowest_frame(self.process_qs)

                if index != -1:
                # If the swapper thread has finished generating a frame
                    if self.process_qs[index]['Status'] == 'finished':
                        image = self.process_qs[index]['ProcessedFrame']

                        if self.parameters['RecordTypeTextSel']=='FFMPEG':

                            pil_image = Image.fromarray(image)
                            pil_image.save(self.sp.stdin, 'BMP')

                        elif self.parameters['RecordTypeTextSel']=='OPENCV':
                            self.sp.write(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

                        temp = [image, self.process_qs[index]['FrameNumber']]
                        self.frame_q.append(temp)

                        # Close video and process
                        if self.process_qs[index]['FrameNumber'] >= self.video_frame_total-1 or self.process_qs[index]['FrameNumber'] == self.stop_marker or self.play == False:
                            self.play_video("stop")
                            stop_time = float(self.capture.get(cv2.CAP_PROP_POS_FRAMES) / float(self.fps))
                            if stop_time == 0:
                                stop_time = float(self.video_frame_total) / float(self.fps)

                            if self.parameters['RecordTypeTextSel']=='FFMPEG':
                                self.sp.stdin.close()
                                self.sp.wait()
                            elif self.parameters['RecordTypeTextSel']=='OPENCV':
                                self.sp.release()

                            orig_file = self.target_video
                            final_file = self.output+self.file_name[1]
                            print("adding audio...")
                            args = ["ffmpeg",
                                    '-hide_banner',
                                    '-loglevel',    'error',
                                    "-i", self.temp_file,
                                    "-ss", str(self.start_time), "-to", str(stop_time), "-i",  orig_file,
                                    "-c",  "copy", # may be c:v
                                    "-map", "0:v:0", "-map", "1:a:0?",
                                    "-shortest",
                                    final_file]

                            four = subprocess.run(args)
                            os.remove(self.temp_file)

                            timef= time.time() - self.timer
                            self.record = False
                            print('Video saved as:', final_file)
                            msg = "Total time: %s s." % (round(timef,1))
                            print(msg)

                        self.total_thread_time = []
                        self.process_qs[index]['Status'] = 'clear'
                        self.process_qs[index]['FrameNumber'] = []
                        self.process_qs[index]['Thread'] = []
                        self.frame_timer = time.time()
        else:
            self.record=False
            self.add_action('disable_record_button', False)

    # @profile
    def thread_video_read(self, frame_number):
        with lock:
            success, target_image = self.capture.read()

        if success:
            target_image = cv2.cvtColor(target_image, cv2.COLOR_BGR2RGB)
            if not self.control['SwapFacesButton'] and not self.control['EditFacesButton']:
                temp = [target_image, frame_number]

            else:
                temp = [self.swap_video(target_image, frame_number, True), frame_number]

            if self.control['EnhanceFrameButton']:
                temp[0] = self.enhance_video(temp[0], frame_number, True)

            for item in self.process_qs:
                if item['FrameNumber'] == frame_number:
                    item['ProcessedFrame'] = temp[0]
                    item['Status'] = 'finished'
                    item['ThreadTime'] = time.time() - item['ThreadTime']
                    break

    def enhance_video(self, target_image, frame_number, use_markers):
        # Grab a local copy of the parameters to prevent threading issues
        parameters = self.parameters.copy()
        control = self.control.copy()

        # Find out if the frame is in a marker zone and copy the parameters if true
        if self.markers and use_markers:
            temp=[]
            for i in range(len(self.markers)):
                temp.append(self.markers[i]['frame'])
            idx = bisect.bisect(temp, frame_number)

            # We copy marker parameters only if condition matches.
            if idx > 0:
                parameters = self.markers[idx-1]['parameters'].copy()

        # Load frame into VRAM
        img = torch.from_numpy(target_image.astype('uint8')).to('cuda') #HxWxc
        img = img.permute(2,0,1)#cxHxW

        img = self.func_w_test("enhance_video", self.enhance_core, img, parameters)

        img = img.permute(1,2,0)
        img = img.cpu().numpy()

        return img

    def enhance_core(self, img, parameters):
        enhancer_type = parameters['FrameEnhancerTypeTextSel']

        match enhancer_type:
            case 'RealEsrgan-x2-Plus' | 'RealEsrgan-x4-Plus' | 'BSRGan-x2' | 'BSRGan-x4' | 'UltraSharp-x4' | 'UltraMix-x4' | 'RealEsr-General-x4v3':
                tile_size = 512

                if enhancer_type == 'RealEsrgan-x2-Plus' or enhancer_type == 'BSRGan-x2':
                    scale = 2
                else:
                    scale = 4

                image = img.type(torch.float32)
                if torch.max(image) > 256:  # 16-bit image
                    max_range = 65535
                else:
                    max_range = 255

                image = torch.div(image, max_range)
                image = torch.unsqueeze(image, 0).contiguous()

                image = self.models.run_enhance_frame_tile_process(image, enhancer_type, tile_size=tile_size, scale=scale)

                image = torch.squeeze(image)
                image = torch.clamp(image, 0, 1)
                image = torch.mul(image, max_range)

                # Blend
                alpha = float(parameters["EnhancerSlider"])/100.0

                t_scale = v2.Resize((img.shape[1] * scale, img.shape[2] * scale), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)
                img = t_scale(img)
                img = torch.add(torch.mul(image, alpha), torch.mul(img, 1-alpha))
                if max_range == 255:
                    img = img.type(torch.uint8)
                else:
                    img = img.type(torch.uint16)

            case 'DeOldify-Artistic' | 'DeOldify-Stable' | 'DeOldify-Video':
                render_factor = 384 # 12 * 32 | highest quality = 20 * 32 == 640

                channels, h, w = img.shape
                t_resize_i = v2.Resize((render_factor, render_factor), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)
                image = t_resize_i(img)

                image = image.type(torch.float32)
                image = torch.unsqueeze(image, 0).contiguous()

                output = torch.empty((image.shape), dtype=torch.float32, device='cuda').contiguous()

                match enhancer_type:
                    case 'DeOldify-Artistic':
                        self.models.run_deoldify_artistic(image, output)
                    case 'DeOldify-Stable':
                        self.models.run_deoldify_stable(image, output)
                    case 'DeOldify-Video':
                        self.models.run_deoldify_video(image, output)

                output = torch.squeeze(output)
                t_resize_o = v2.Resize((h, w), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)
                output = t_resize_o(output)

                output = faceutil.rgb_to_yuv(output, True)
                # do a black and white transform first to get better luminance values
                hires = faceutil.rgb_to_yuv(img, True)

                hires[1:3, :, :] = output[1:3, :, :]
                hires = faceutil.yuv_to_rgb(hires, True)

                # Blend
                alpha = float(parameters["EnhancerSlider"]) / 100.0
                img = torch.add(torch.mul(hires, alpha), torch.mul(img, 1-alpha))

                img = img.type(torch.uint8)

            case 'DDColor-Artistic' | 'DDColor':
                render_factor = 384 # 12 * 32 | highest quality = 20 * 32 == 640

                # Converti RGB a LAB
                '''
                orig_l = img.permute(1, 2, 0).cpu().numpy()
                orig_l = cv2.cvtColor(orig_l, cv2.COLOR_RGB2Lab)
                orig_l = torch.from_numpy(orig_l).to('cuda')
                orig_l = orig_l.permute(2, 0, 1)
                '''
                orig_l = faceutil.rgb_to_lab(img, True)

                orig_l = orig_l[0:1, :, :]  # (1, h, w)

                # Resize per il modello
                t_resize_i = v2.Resize((render_factor, render_factor), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)
                image = t_resize_i(img)

                # Converti RGB in LAB
                '''
                img_l = image.permute(1, 2, 0).cpu().numpy()
                img_l = cv2.cvtColor(img_l, cv2.COLOR_RGB2Lab)
                img_l = torch.from_numpy(img_l).to('cuda')
                img_l = img_l.permute(2, 0, 1)
                '''
                img_l = faceutil.rgb_to_lab(image, True)

                img_l = img_l[0:1, :, :]  # (1, render_factor, render_factor)
                img_gray_lab = torch.cat((img_l, torch.zeros_like(img_l), torch.zeros_like(img_l)), dim=0)  # (3, render_factor, render_factor)

                # Converti LAB in RGB
                '''
                img_gray_lab = img_gray_lab.permute(1, 2, 0).cpu().numpy()
                img_gray_rgb = cv2.cvtColor(img_gray_lab, cv2.COLOR_LAB2RGB)
                img_gray_rgb = torch.from_numpy(img_gray_rgb).to('cuda')
                img_gray_rgb = img_gray_rgb.permute(2, 0, 1)
                '''
                img_gray_rgb = faceutil.lab_to_rgb(img_gray_lab)

                tensor_gray_rgb = torch.unsqueeze(img_gray_rgb.type(torch.float32), 0).contiguous()

                # Prepara il tensore per il modello
                output_ab = torch.empty((1, 2, render_factor, render_factor), dtype=torch.float32, device='cuda')

                # Esegui il modello
                match enhancer_type:
                    case 'DDColor-Artistic':
                        self.models.run_ddcolor_artistic(tensor_gray_rgb, output_ab)
                    case 'DDColor':
                        self.models.run_ddcolor(tensor_gray_rgb, output_ab)

                output_ab = output_ab.squeeze(0)  # (2, render_factor, render_factor)

                t_resize_o = v2.Resize((img.size(1), img.size(2)), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)
                output_lab_resize = t_resize_o(output_ab)

                # Combina il canale L originale con il risultato del modello
                output_lab = torch.cat((orig_l, output_lab_resize), dim=0)  # (3, original_H, original_W)

                # Convert LAB to RGB
                '''
                output_rgb = output_lab.permute(1, 2, 0).cpu().numpy()
                output_rgb = cv2.cvtColor(output_rgb, cv2.COLOR_Lab2RGB)
                output_rgb = torch.from_numpy(output_rgb).to('cuda')
                output_rgb = output_rgb.permute(2, 0, 1)
                '''
                output_rgb = faceutil.lab_to_rgb(output_lab, True)  # (3, original_H, original_W)

                # Miscela le immagini
                alpha = float(parameters["EnhancerSlider"]) / 100.0
                blended_img = torch.add(torch.mul(output_rgb, alpha), torch.mul(img, 1 - alpha))

                # Converti in uint8
                img = blended_img.type(torch.uint8)

        return img

    # @profile
    def swap_video(self, target_image, frame_number, use_markers):
        # Grab a local copy of the parameters to prevent threading issues
        parameters = self.parameters.copy()
        control = self.control.copy()

        # Find out if the frame is in a marker zone and copy the parameters if true
        if self.markers and use_markers:
            temp=[]
            for i in range(len(self.markers)):
                temp.append(self.markers[i]['frame'])
            idx = bisect.bisect(temp, frame_number)

            # We copy marker parameters only if condition matches.
            if idx > 0:
                parameters = self.markers[idx-1]['parameters'].copy()

        # Load frame into VRAM
        img = torch.from_numpy(target_image.astype('uint8')).to('cuda') #HxWxc
        img = img.permute(2,0,1)#cxHxW

        #Scale up frame if it is smaller than 512
        img_x = img.size()[2]
        img_y = img.size()[1]

        det_scale = 1.0
        if img_x<512 and img_y<512:
            # if x is smaller, set x to 512
            if img_x <= img_y:
                new_height = int(512*img_y/img_x)
                tscale = v2.Resize((new_height, 512), antialias=False)
            else:
                new_height = 512
                tscale = v2.Resize((new_height, int(512*img_x/img_y)), antialias=False)

            img = tscale(img)

            det_scale = torch.div(new_height, img_y)

        elif img_x<512:
            new_height = int(512*img_y/img_x)
            tscale = v2.Resize((new_height, 512), antialias=False)
            img = tscale(img)

            det_scale = torch.div(new_height, img_y)

        elif img_y<512:
            new_height = 512
            tscale = v2.Resize((new_height, int(512*img_x/img_y)), antialias=False)
            img = tscale(img)

            det_scale = torch.div(new_height, img_y)

        # Rotate the frame
        if parameters['OrientSwitch']:
            img = v2.functional.rotate(img, angle=parameters['OrientSlider'], interpolation=v2.InterpolationMode.BILINEAR, expand=True)

        # Find all faces in frame and return a list of 5-pt kpss
        if parameters["AutoRotationSwitch"]:
            rotation_angles = [0, 90, 180, 270]
        else:
            rotation_angles = [0]

        use_landmark_detection=parameters['LandmarksDetectionAdjSwitch']
        landmark_detect_mode=parameters["LandmarksDetectTypeTextSel"]
        from_points=parameters["LandmarksAlignModeFromPointsSwitch"]
        if control['EditFacesButton']:
            if not use_landmark_detection or landmark_detect_mode=="5":
                # force to use landmark detector when edit face is enabled.
                use_landmark_detection = True
                landmark_detect_mode = "106"

            # force to use from_points in landmark detector when edit face is enabled.
            from_points = True

        bboxes, kpss_5, kpss = self.func_w_test("detect", self.models.run_detect, img, parameters['DetectTypeTextSel'], max_num=20, score=parameters['DetectScoreSlider']/100.0, use_landmark_detection=use_landmark_detection, landmark_detect_mode=landmark_detect_mode, landmark_score=parameters["LandmarksDetectScoreSlider"]/100.0, from_points=from_points, rotation_angles=rotation_angles)

        # Set Max FaceID to FaceLandmarks and FaceEditor widgets
        if kpss_5 is not None and len(kpss_5) > 0:
            self.face_landmarks.apply_max_face_id_to_widget(frame_number, kpss_5.shape[0])
            self.face_editor.apply_max_face_id_to_widget(frame_number, kpss_5.shape[0])
        else:
            self.face_landmarks.apply_max_face_id_to_widget(frame_number, 1)
            self.face_editor.apply_max_face_id_to_widget(frame_number, 1)
        #

        # Get embeddings for all faces found in the frame
        ret = []
        # Face Landmarks
        if kpss_5 is not None and len(kpss_5) > 0:
            for i in range(kpss_5.shape[0]):
                face_kps_5 = kpss_5[i]
                face_kps = kpss[i]
                # Face Landmarks
                if self.face_landmarks and parameters['LandmarksPositionAdjSwitch']:
                    landmarks = self.face_landmarks.get_landmarks(frame_number, i + 1)
                    if landmarks is not None:
                        # Change the ref points
                        if parameters['FaceAdjSwitch']:
                            face_kps_5[:,0] += parameters['KPSXSlider']
                            face_kps_5[:,1] += parameters['KPSYSlider']
                            face_kps_5[:,0] -= 255
                            face_kps_5[:,0] *= (1+parameters['KPSScaleSlider']/100)
                            face_kps_5[:,0] += 255
                            face_kps_5[:,1] -= 255
                            face_kps_5[:,1] *= (1+parameters['KPSScaleSlider']/100)
                            face_kps_5[:,1] += 255

                        face_kps_5[0][0] += landmarks[0][0]
                        face_kps_5[0][1] += landmarks[0][1]
                        face_kps_5[1][0] += landmarks[1][0]
                        face_kps_5[1][1] += landmarks[1][1]
                        face_kps_5[2][0] += landmarks[2][0]
                        face_kps_5[2][1] += landmarks[2][1]
                        face_kps_5[3][0] += landmarks[3][0]
                        face_kps_5[3][1] += landmarks[3][1]
                        face_kps_5[4][0] += landmarks[4][0]
                        face_kps_5[4][1] += landmarks[4][1]
                #

                if control['SwapFacesButton']:
                    face_emb, _ = self.func_w_test('recognize',  self.models.run_recognize, img, face_kps_5, self.parameters["SimilarityTypeTextSel"], self.parameters['FaceSwapperModelTextSel'])
                    ret.append([face_kps_5, face_kps, face_emb])
                else:
                    ret.append([face_kps_5, face_kps, None])

        if ret:
            # Loop through target faces to see if they match our found face embeddings
            for i, fface in enumerate(ret):
                if control['SwapFacesButton']:
                    for found_face in self.found_faces:
                        # sim between face in video and already found face
                        sim = self.findCosineDistance(fface[2], found_face["Embedding"])
                        # if the face[i] in the frame matches afound face[j] AND the found face is active (not [])
                        if sim>=float(parameters["ThresholdSlider"]) and found_face["SourceFaceAssignments"]:
                            s_e = found_face["AssignedEmbedding"]
                            # img_orig = torch.clone(img)
                            # s_e = found_face['ptrdata']
                            img = self.func_w_test("swap_video", self.swap_core, img, fface[0], fface[1], s_e, fface[2], found_face.get('DFLModel', False), parameters, control)
                            # img = img.permute(2,0,1)

                if control['EditFacesButton']:
                    parameters_face_editor = self.face_editor.get_named_parameters(frame_number, i + 1)
                    img = self.func_w_test("swap_video", self.swap_edit_face_core, img, fface[1], parameters, parameters_face_editor.copy(), control)

            img = img.permute(1,2,0)
            if not control['MaskViewButton'] and parameters['OrientSwitch']:
                img = img.permute(2,0,1)
                img = transforms.functional.rotate(img, angle=-parameters['OrientSlider'], expand=True)
                img = img.permute(1,2,0)

        else:
            img = img.permute(1,2,0)
            if parameters['OrientSwitch']:
                img = img.permute(2,0,1)
                img = v2.functional.rotate(img, angle=-parameters['OrientSlider'], interpolation=v2.InterpolationMode.BILINEAR, expand=True)
                img = img.permute(1,2,0)

        if self.perf_test:
            print('------------------------')

        # Unscale small videos
        if img_x <512 or img_y < 512:
            tscale = v2.Resize((img_y, img_x), antialias=False)
            img = img.permute(2,0,1)
            img = tscale(img)
            img = img.permute(1,2,0)

        img = img.cpu().numpy()

        if parameters["ShowLandmarksSwitch"]:
            if ret:
                if img_y <= 720:
                    p = 1
                else:
                    p = 2

                for i, face in enumerate(ret):
                    if parameters['LandmarksPositionAdjSwitch'] and parameters['FaceIDSlider'] == i + 1:
                        kcolor = tuple((255, 0, 0))
                        keypoints = face[0]

                    else:
                        kcolor = tuple((0, 255, 255))
                        keypoints = face[1]

                    # Change the ref points
                    if parameters['FaceAdjSwitch']:
                        keypoints[:,0] += parameters['KPSXSlider']
                        keypoints[:,1] += parameters['KPSYSlider']
                        keypoints[:,0] -= 255
                        keypoints[:,0] *= (1+parameters['KPSScaleSlider']/100)
                        keypoints[:,0] += 255
                        keypoints[:,1] -= 255
                        keypoints[:,1] *= (1+parameters['KPSScaleSlider']/100)
                        keypoints[:,1] += 255

                    for kpoint in keypoints:
                        kpoint = kpoint / det_scale
                        for i in range(-1, p):
                            for j in range(-1, p):
                                try:
                                    img[int(kpoint[1])+i][int(kpoint[0])+j][0] = kcolor[0]
                                    img[int(kpoint[1])+i][int(kpoint[0])+j][1] = kcolor[1]
                                    img[int(kpoint[1])+i][int(kpoint[0])+j][2] = kcolor[2]
                                except:
                                    #print("Key-points value {} exceed the image size {}.".format(kpoint, (img_x, img_y)))
                                    continue

        return img.astype(np.uint8)

    def findCosineDistance(self, vector1, vector2):
        vector1 = vector1.ravel()
        vector2 = vector2.ravel()
        cos_dist = 1.0 - np.dot(vector1, vector2)/(np.linalg.norm(vector1)*np.linalg.norm(vector2)) # 2..0

        return 100.0-cos_dist*50.0
        '''
        vector1 = vector1.ravel()
        vector2 = vector2.ravel()

        return 1 - np.dot(vector1, vector2)/(np.linalg.norm(vector1)*np.linalg.norm(vector2))
        '''

    def func_w_test(self, name, func, *args, **argsv):
        timing = time.time()
        result = func(*args, **argsv)
        if self.perf_test:
            print(name, round(time.time()-timing, 5), 's')
        return result

    # @profile
    def swap_core(self, img, kps_5, kps, s_e, t_e, dfl_model, parameters, control): # img = RGB
        swapper_model = parameters['FaceSwapperModelTextSel']

        if dfl_model:
            if not self.models.dfl_models.get(dfl_model):
                try:
                    self.models.dfl_models[dfl_model] = DFMModel(f'./dfl_models/{dfl_model}', self.models.providers)
                except Exception as e:
                    print(e)
                    return img

            dfl_model = self.models.dfl_models[dfl_model]

        # Set Default for DEVELOPMENT
        # swapper_model = 'DFL'

        if swapper_model != 'GhostFace-v1' and swapper_model != 'GhostFace-v2' and swapper_model != 'GhostFace-v3':
            # 512 transforms
            dst = self.arcface_dst * 4.0
            dst[:,0] += 32.0

            # Change the ref points
            if parameters['FaceAdjSwitch']:
                dst[:,0] += parameters['KPSXSlider']
                dst[:,1] += parameters['KPSYSlider']
                dst[:,0] -= 255
                dst[:,0] *= (1+parameters['KPSScaleSlider']/100)
                dst[:,0] += 255
                dst[:,1] -= 255
                dst[:,1] *= (1+parameters['KPSScaleSlider']/100)
                dst[:,1] += 255

            tform = trans.SimilarityTransform()
            tform.estimate(kps_5, dst)
        else:
            dst = faceutil.get_arcface_template(image_size=512, mode='arcfacemap')

            # Change the ref points
            if parameters['FaceAdjSwitch']:
                for k in dst:
                    k[:,0] += parameters['KPSXSlider']
                    k[:,1] += parameters['KPSYSlider']
                    k[:,0] -= 255
                    k[:,0] *= (1+parameters['KPSScaleSlider']/100)
                    k[:,0] += 255
                    k[:,1] -= 255
                    k[:,1] *= (1+parameters['KPSScaleSlider']/100)
                    k[:,1] += 255

            M, _ = faceutil.estimate_norm_arcface_template(kps_5, src=dst)
            tform = trans.SimilarityTransform()
            tform.params[0:2] = M

        # Scaling Transforms
        t512 = v2.Resize((512, 512), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)
        t256 = v2.Resize((256, 256), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)
        t128 = v2.Resize((128, 128), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)

        # Grab 512 face from image and create 256 and 128 copys
        original_face_512 = v2.functional.affine(img, tform.rotation*57.2958, (tform.translation[0], tform.translation[1]) , tform.scale, 0, center = (0,0), interpolation=v2.InterpolationMode.BILINEAR )
        original_face_512 = v2.functional.crop(original_face_512, 0,0, 512, 512)# 3, 512, 512
        original_face_256 = t256(original_face_512)
        original_face_128 = t128(original_face_256)

        if dfl_model:
            latent = torch.from_numpy(self.models.calc_swapper_latent_dfl(s_e)).float().to('cuda')
            input_face_affined = original_face_512
            dim = 4

        else:
            if swapper_model == 'Inswapper128':
                latent = torch.from_numpy(self.models.calc_swapper_latent(s_e)).float().to('cuda')
                if parameters['FaceLikenessSwitch']:
                    factor = parameters['FaceLikenessFactorSlider']
                    dst_latent = torch.from_numpy(self.models.calc_swapper_latent(t_e)).float().to('cuda')
                    latent = latent - (factor * dst_latent)

                dim = 1
                if parameters['SwapperTypeTextSel'] == '128':
                    dim = 1
                    input_face_affined = original_face_128
                elif parameters['SwapperTypeTextSel'] == '256':
                    dim = 2
                    input_face_affined = original_face_256
                elif parameters['SwapperTypeTextSel'] == '512':
                    dim = 4
                    input_face_affined = original_face_512

            elif swapper_model == 'SimSwap512':
                latent = torch.from_numpy(self.models.calc_swapper_latent_simswap512(s_e)).float().to('cuda')
                if parameters['FaceLikenessSwitch']:
                    factor = parameters['FaceLikenessFactorSlider']
                    dst_latent = torch.from_numpy(self.models.calc_swapper_latent_simswap512(t_e)).float().to('cuda')
                    latent = latent - (factor * dst_latent)

                dim = 4
                input_face_affined = original_face_512

            elif swapper_model == 'GhostFace-v1' or swapper_model == 'GhostFace-v2' or swapper_model == 'GhostFace-v3':
                latent = torch.from_numpy(self.models.calc_swapper_latent_ghost(s_e)).float().to('cuda')
                if parameters['FaceLikenessSwitch']:
                    factor = parameters['FaceLikenessFactorSlider']
                    dst_latent = torch.from_numpy(self.models.calc_swapper_latent_ghost(t_e)).float().to('cuda')
                    latent = latent - (factor * dst_latent)

                dim = 2
                input_face_affined = original_face_256

        # Optional Scaling # change the transform matrix scaling from center
        if parameters['FaceAdjSwitch']:
            input_face_affined = v2.functional.affine(input_face_affined, 0, (0, 0), 1 + parameters['FaceScaleSlider'] / 100, 0, center=(dim*128/2, dim*128/2), interpolation=v2.InterpolationMode.BILINEAR)

        itex = 1
        if parameters['StrengthSwitch']:
            itex = ceil(parameters['StrengthSlider'] / 100.)

        if dfl_model:
            output_size = dfl_model._input_height
        else:
            output_size = int(128 * dim)

        output = torch.zeros((output_size, output_size, 3), dtype=torch.float32, device='cuda')
        input_face_affined = input_face_affined.permute(1, 2, 0)
        input_face_affined = torch.div(input_face_affined, 255.0)

        if dfl_model:
            # Get face alignment image processor
            fai_ip = dfl_model.get_fai_ip(original_face_512.permute(1, 2, 0).cpu().numpy())
            test_swap = fai_ip.get_image('HWC')

            # Convert and obtain outputs
            out_celeb, out_celeb_mask, out_face_mask = dfl_model.convert(test_swap, parameters['DFLAmpMorphSlider']/100, rct=parameters['DFLRCTColorSwitch'])

            swapper_output = torch.from_numpy(out_celeb.copy()).cuda()
            # swapper_output = swapper_output.permute(1, 2, 0)

            prev_face = input_face_affined.clone()
            input_face_affined = swapper_output.clone()

            # show_image(prev_face)
            # show_image(swapper_output)
            output = swapper_output.clone()

            # output = color_transfer(prev_face, output)
        else:
            if swapper_model == 'Inswapper128':
                with torch.no_grad():  # Disabilita il calcolo del gradiente se è solo per inferenza
                    for k in range(itex):
                        for j in range(dim):
                            for i in range(dim):
                                input_face_disc = input_face_affined[j::dim,i::dim]
                                input_face_disc = input_face_disc.permute(2, 0, 1)
                                input_face_disc = torch.unsqueeze(input_face_disc, 0).contiguous()

                                swapper_output = torch.empty((1,3,128,128), dtype=torch.float32, device='cuda').contiguous()
                                self.models.run_swapper(input_face_disc, latent, swapper_output)

                                swapper_output = torch.squeeze(swapper_output)
                                swapper_output = swapper_output.permute(1, 2, 0)

                                output[j::dim, i::dim] = swapper_output.clone()
                        prev_face = input_face_affined.clone()
                        input_face_affined = output.clone()
                        output = torch.mul(output, 255)
                        output = torch.clamp(output, 0, 255)

            elif swapper_model == 'SimSwap512':
                for k in range(itex):
                    input_face_disc = input_face_affined.permute(2, 0, 1)
                    input_face_disc = torch.unsqueeze(input_face_disc, 0).contiguous()
                    swapper_output = torch.empty((1,3,512,512), dtype=torch.float32, device='cuda').contiguous()
                    self.models.run_swapper_simswap512(input_face_disc, latent, swapper_output)
                    swapper_output = torch.squeeze(swapper_output)
                    swapper_output = swapper_output.permute(1, 2, 0)
                    prev_face = input_face_affined.clone()
                    input_face_affined = swapper_output.clone()

                    output = swapper_output.clone()
                    output = torch.mul(output, 255)
                    output = torch.clamp(output, 0, 255)

            elif swapper_model == 'GhostFace-v1' or swapper_model == 'GhostFace-v2' or swapper_model == 'GhostFace-v3':
                for k in range(itex):
                    input_face_disc = torch.mul(input_face_affined, 255.0).permute(2, 0, 1)
                    input_face_disc = torch.div(input_face_disc.float(), 127.5)
                    input_face_disc = torch.sub(input_face_disc, 1)
                    #input_face_disc = input_face_disc[[2, 1, 0], :, :] # Inverte i canali da BGR a RGB (assumendo che l'input sia BGR)
                    input_face_disc = torch.unsqueeze(input_face_disc, 0).contiguous()
                    swapper_output = torch.empty((1,3,256,256), dtype=torch.float32, device='cuda').contiguous()
                    self.models.run_swapper_ghostface(input_face_disc, latent, swapper_output, swapper_model)
                    swapper_output = swapper_output[0]
                    swapper_output = swapper_output.permute(1, 2, 0)
                    swapper_output = torch.mul(swapper_output, 127.5)
                    swapper_output = torch.add(swapper_output, 127.5)
                    #swapper_output = swapper_output[:, :, [2, 1, 0]] # Inverte i canali da RGB a BGR (assumendo che l'input sia RGB)
                    prev_face = input_face_affined.clone()
                    input_face_affined = swapper_output.clone()
                    input_face_affined = torch.div(input_face_affined, 255)

                    output = swapper_output.clone()
                    output = torch.clamp(output, 0, 255)

        output = output.permute(2, 0, 1)

        swap = t512(output)

        if parameters['StrengthSwitch']:
            if itex == 0:
                swap = original_face_512.clone()
            else:
                alpha = np.mod(parameters['StrengthSlider'], 100)*0.01
                if alpha==0:
                    alpha=1

                # Blend the images
                prev_face = torch.mul(prev_face, 255)
                prev_face = torch.clamp(prev_face, 0, 255)
                prev_face = prev_face.permute(2, 0, 1)
                prev_face = t512(prev_face)
                swap = torch.mul(swap, alpha)
                prev_face = torch.mul(prev_face, 1-alpha)
                swap = torch.add(swap, prev_face)

        # Create border mask
        border_mask = torch.ones((128, 128), dtype=torch.float32, device=device)
        border_mask = torch.unsqueeze(border_mask,0)

        # if parameters['BorderState']:
        top = parameters['BorderTopSlider']
        left = parameters['BorderLeftSlider']
        right = 128-parameters['BorderRightSlider']
        bottom = 128-parameters['BorderBottomSlider']

        border_mask[:, :top, :] = 0
        border_mask[:, bottom:, :] = 0
        border_mask[:, :, :left] = 0
        border_mask[:, :, right:] = 0

        gauss = transforms.GaussianBlur(parameters['BorderBlurSlider']*2+1, (parameters['BorderBlurSlider']+1)*0.2)
        border_mask = gauss(border_mask)

        # Create image mask
        swap_mask = torch.ones((128, 128), dtype=torch.float32, device=device)
        swap_mask = torch.unsqueeze(swap_mask,0)

        # Restorer
        if parameters["RestorerSwitch"]:
            swap = self.func_w_test('Restorer', self.apply_restorer, swap, parameters)

        # Occluder
        if parameters["OccluderSwitch"]:
            mask = self.func_w_test('occluder', self.apply_occlusion , original_face_256, parameters["OccluderSlider"])
            mask = t128(mask)
            swap_mask = torch.mul(swap_mask, mask)
            gauss = transforms.GaussianBlur(parameters['OccluderBlurSlider']*2+1, (parameters['OccluderBlurSlider']+1)*0.2)
            swap_mask = gauss(swap_mask)            

        if parameters["DFLXSegSwitch"]:
            img_mask = self.func_w_test('occluder', self.apply_dfl_xseg , original_face_256, -parameters["DFLXSegSlider"])
            img_mask = t128(img_mask)
            swap_mask = torch.mul(swap_mask, 1 - img_mask)
            gauss = transforms.GaussianBlur(parameters['OccluderBlurSlider']*2+1, (parameters['OccluderBlurSlider']+1)*0.2)
            swap_mask = gauss(swap_mask)
                
        if parameters["FaceParserSwitch"]:
            mask = self.apply_face_parser(swap, parameters)
            mask = t128(mask)
            swap_mask = torch.mul(swap_mask, mask)

        if parameters['RestoreMouthSwitch'] or parameters['RestoreEyesSwitch']:
            M = tform.params[0:2]
            ones_column = np.ones((kps_5.shape[0], 1), dtype=np.float32)
            homogeneous_kps = np.hstack([kps_5, ones_column])
            dst_kps_5 = np.dot(homogeneous_kps, M.T)

            img_swap_mask = torch.ones((1, 512, 512), dtype=torch.float32, device=device).contiguous()
            img_orig_mask = torch.zeros((1, 512, 512), dtype=torch.float32, device=device).contiguous()

            if parameters['RestoreMouthSwitch']:
                img_swap_mask = self.restore_mouth(img_orig_mask, img_swap_mask, dst_kps_5, parameters['ParserBlurSlider'], parameters['RestoreMouthSlider']/100, parameters['RestoreMouthFeatherSlider'], parameters['RestoreMouthSizeSlider']/100, parameters['RestoreMouthRadiusFactorXSlider'], parameters['RestoreMouthRadiusFactorYSlider'])
                img_swap_mask = torch.clamp(img_swap_mask, 0, 1)

            if parameters['RestoreEyesSwitch']:
                img_swap_mask = self.restore_eyes(img_orig_mask, img_swap_mask, dst_kps_5, parameters['ParserBlurSlider'], parameters['RestoreEyesSlider']/100, parameters['RestoreEyesFeatherSlider'], parameters['RestoreEyesSizeSlider'],  parameters['RestoreEyesRadiusFactorXSlider'], parameters['RestoreEyesRadiusFactorYSlider'])
                img_swap_mask = torch.clamp(img_swap_mask, 0, 1)

            img_swap_mask = t128(img_swap_mask)
            swap_mask = torch.mul(swap_mask, img_swap_mask)

        # CLIPs
        if parameters["CLIPSwitch"]:
            with lock:
                mask = self.func_w_test('CLIP', self.apply_CLIPs, original_face_512, parameters["CLIPTextEntry"], parameters["CLIPSlider"])
            mask = cv2.resize(mask, (128,128))
            mask = torch.from_numpy(mask).to('cuda')
            swap_mask *= mask
            
        # Restorer
        if parameters["Restorer2Switch"]:
            swap = self.func_w_test('Restorer2', self.apply_restorer2, swap, parameters)    
          


        if parameters["AutoColorSwitch"]:
            # Histogram color matching original face on swapped face
            if parameters['AutoColorTypeTextSel'] == 'Test':
                swap = self.histogram_matching(original_face_512, swap, parameters["AutoColorSlider"])
 
            elif parameters['AutoColorTypeTextSel'] == 'Test_Mask':
                swap = self.histogram_matching_withmask(original_face_512, swap, t512(swap_mask), parameters["AutoColorSlider"])
            
            elif parameters['AutoColorTypeTextSel'] == 'DFL_Test':
                swap = self.histogram_matching_DFL_test(original_face_512, swap, parameters["AutoColorSlider"])
                    
            elif parameters['AutoColorTypeTextSel'] == 'DFL_Orig':
                swap = self.histogram_matching_DFL_Orig(original_face_512, swap, t512(swap_mask), parameters["AutoColorSlider"])
 
            #swap = torch.clamp(swap, 0, 255)                

        # Face Diffing
        if parameters["DiffSwitch"]:
            mask = self.apply_fake_diff(swap, original_face_512, parameters["DiffSlider"])
            # mask = t128(mask)
            gauss = transforms.GaussianBlur(parameters['DiffingBlurSlider']*2+1, (parameters['DiffingBlurSlider']+1)*0.2)
            mask = gauss(mask.type(torch.float32))
            swap = swap*mask + original_face_512*(1-mask)             

        # Add blur to swap_mask results
        #gauss = transforms.GaussianBlur(parameters['BlendSlider']*2+1, (parameters['BlendSlider']+1)*0.2)
        #swap_mask = gauss(swap_mask)
        
        # Apply color corerctions
        if parameters['ColorSwitch']:
            # print(parameters['ColorGammaSlider'])
            swap = torch.unsqueeze(swap,0).contiguous()
            swap = v2.functional.adjust_gamma(swap, parameters['ColorGammaSlider'], 1.0)
            swap = torch.squeeze(swap)
            swap = swap.permute(1, 2, 0).type(torch.float32)

            del_color = torch.tensor([parameters['ColorRedSlider'], parameters['ColorGreenSlider'], parameters['ColorBlueSlider']], device=device)
            swap += del_color
            swap = torch.clamp(swap, min=0., max=255.)
            swap = swap.permute(2, 0, 1).type(torch.uint8)

            swap = v2.functional.adjust_brightness(swap, parameters['ColorBrightSlider'])
            swap = v2.functional.adjust_contrast(swap, parameters['ColorContrastSlider'])
            swap = v2.functional.adjust_saturation(swap, parameters['ColorSaturationSlider'])
            swap = v2.functional.adjust_sharpness(swap, parameters['ColorSharpnessSlider'])
            swap = v2.functional.adjust_hue(swap, parameters['ColorHueSlider'])
        
        if parameters['FinalBlurSlider'] > 0:
            final_blur_strength = parameters["FinalBlurSlider"]  # Ein Parameter steuert beides
            # Bestimme kernel_size und sigma basierend auf dem Parameter
            kernel_size = 2 * final_blur_strength + 1  # Ungerade Zahl, z.B. 3, 5, 7, ...
            sigma = final_blur_strength * 0.3  # Sigma proportional zur Stärke
            # Gaussian Blur anwenden
            gaussian_blur = transforms.GaussianBlur(kernel_size=kernel_size, sigma=sigma)
            swap = gaussian_blur(swap)   

        if parameters['JpegCompressionSwitch']:   
        #if parameters["DiffSlider"] > 0:
            swap = self.jpegBlur(swap, parameters["JpegCompressionSlider"])
            #swap = swap#.type(torch.float32)  
            swap = torch.clamp(swap, 0, 255)             

        # Combine border and swap mask, scale, and apply to swap
        swap_mask = torch.mul(swap_mask, border_mask)
        swap_mask = t512(swap_mask)
        swap = torch.mul(swap, swap_mask)

        if not control['MaskViewButton'] and not control['CompareViewButton']:
            # Cslculate the area to be mergerd back to the original frame
            IM512 = tform.inverse.params[0:2, :]
            corners = np.array([[0,0], [0,511], [511, 0], [511, 511]])

            x = (IM512[0][0]*corners[:,0] + IM512[0][1]*corners[:,1] + IM512[0][2])
            y = (IM512[1][0]*corners[:,0] + IM512[1][1]*corners[:,1] + IM512[1][2])

            left = floor(np.min(x))
            if left<0:
                left=0
            top = floor(np.min(y))
            if top<0:
                top=0
            right = ceil(np.max(x))
            if right>img.shape[2]:
                right=img.shape[2]
            bottom = ceil(np.max(y))
            if bottom>img.shape[1]:
                bottom=img.shape[1]

            # Untransform the swap
            swap = v2.functional.pad(swap, (0,0,img.shape[2]-512, img.shape[1]-512))
            swap = v2.functional.affine(swap, tform.inverse.rotation*57.2958, (tform.inverse.translation[0], tform.inverse.translation[1]), tform.inverse.scale, 0,interpolation=v2.InterpolationMode.BILINEAR, center = (0,0) )
            swap = swap[0:3, top:bottom, left:right]
            swap = swap.permute(1, 2, 0)

            # Untransform the swap mask
            swap_mask = v2.functional.pad(swap_mask, (0,0,img.shape[2]-512, img.shape[1]-512))
            swap_mask = v2.functional.affine(swap_mask, tform.inverse.rotation*57.2958, (tform.inverse.translation[0], tform.inverse.translation[1]), tform.inverse.scale, 0, interpolation=v2.InterpolationMode.BILINEAR, center = (0,0) )
            swap_mask = swap_mask[0:1, top:bottom, left:right]
            swap_mask = swap_mask.permute(1, 2, 0)
            swap_mask = torch.sub(1, swap_mask)

            # Apply the mask to the original image areas
            img_crop = img[0:3, top:bottom, left:right]
            img_crop = img_crop.permute(1,2,0)
            img_crop = torch.mul(swap_mask,img_crop)

            #Add the cropped areas and place them back into the original image
            swap = torch.add(swap, img_crop)
            swap = swap.type(torch.uint8)
            swap = swap.permute(2,0,1)
            img[0:3, top:bottom, left:right] = swap

        elif control['MaskViewButton']:
            # Invert swap mask
            swap_mask = torch.sub(1, swap_mask)

            # Combine preswapped face with swap
            original_face_512 = torch.mul(swap_mask, original_face_512)
            original_face_512 = torch.add(swap, original_face_512)
            original_face_512 = original_face_512.type(torch.uint8)
            original_face_512 = original_face_512.permute(1, 2, 0)

            # Uninvert and create image from swap mask
            swap_mask = torch.sub(1, swap_mask)
            swap_mask = torch.cat((swap_mask,swap_mask,swap_mask),0)
            swap_mask = swap_mask.permute(1, 2, 0)

            # Place them side by side
            img = torch.hstack([original_face_512, swap_mask*255])
            img = img.permute(2,0,1)
            
        else:
            # Invert swap mask
            swap_mask = torch.sub(1, swap_mask)
            
            # Combine preswapped face with swap and prepare original face
            original_face2 = original_face_512.clone()
            original_face_512 = torch.mul(swap_mask, original_face_512)
            original_face_512 = torch.add(swap, original_face_512)            
            original_face_512 = original_face_512.type(torch.uint8)
            original_face_512 = original_face_512.permute(1, 2, 0)
            original_face2 = original_face2.type(torch.uint8)
            original_face2 = original_face2.permute(1, 2, 0)            

            # Place them side by side
            img = torch.hstack([original_face_512, original_face2])
            img = img.permute(2,0,1)  

        return img

    def jpegBlur(self, image, q):
        # Konvertiere Torch-Tensor in CuPy-Array und transponiere zu (512, 512, 3)
        im_cp = cp.asarray(image.permute(1, 2, 0).contiguous().cpu().numpy().astype('uint8'))
        
        # Konvertiere zu uint8, falls noch nicht geschehen
        #im_cp_uint8 = im_cp.astype(cp.uint8)
        
        # Schreibe in einen Puffer als JPEG mit gegebener Qualität
        buf = io.BytesIO()
        imageio.imwrite(buf, cp.asnumpy(im_cp), format='jpeg', quality=q)
        
        # Lese das Bild aus dem Puffer
        buf.seek(0)
        im_cp_blurred = cp.asarray(imageio.imread(buf, format='jpeg')).astype(cp.float32)
        
        # Transponiere zurück zu (3, 512, 512)
        im_cp_blurred = im_cp_blurred.transpose(2, 0, 1)
        
        # Konvertiere das CuPy-Array zurück zu einem Torch-Tensor und auf die GPU
        im_blurred = torch.from_numpy(cp.asnumpy(im_cp_blurred)).to('cuda')
        
        return im_blurred          

    def histogram_matching(self, source_image, target_image, diffslider):
        # convert to CuPy
        source_image_cp = cp.asarray(source_image.permute(1, 2, 0).contiguous().detach().to('cpu').numpy().astype('float32'))
        target_image_cp = cp.asarray(target_image.permute(1, 2, 0).contiguous().detach().to('cpu').numpy().astype('float32'))

        # normalize images [0, 1]
        source_image_cp /= 255.0
        target_image_cp /= 255.0

        matched_target_image_cp = cp.copy(target_image_cp)

        for channel in range(3):
            # Calculate the histograms and cumulative distributions
            source_hist, bin_edges = cp.histogram(source_image_cp[:, :, channel], bins=256, range=(0, 1), density=True)
            target_hist, _ = cp.histogram(target_image_cp[:, :, channel], bins=256, range=(0, 1), density=True)

            source_cdf = cp.cumsum(source_hist)
            target_cdf = cp.cumsum(target_hist)

            # Normalize the CDFs
            source_cdf /= source_cdf[-1]
            target_cdf /= target_cdf[-1]

            # Create a mapping function from target CDF to source CDF
            interp_t_values = cp.interp(target_image_cp[:, :, channel].flatten(), bin_edges[:-1], target_cdf)
            matched_channel = cp.interp(interp_t_values, source_cdf, bin_edges[:-1])

            matched_target_image_cp[:, :, channel] = matched_channel.reshape(target_image_cp[:, :, channel].shape)

        final_image_cp = (1 - diffslider/100) * target_image_cp + diffslider/100 * matched_target_image_cp

        final_image_cp = cp.clip(final_image_cp * 255, 0, 255)#.astype(cp.uint8)

        # Convert the result back to a Torch tensor and onto the original device
        final_image_tensor = torch.from_numpy(cp.asnumpy(final_image_cp.transpose(2, 0, 1))).float().to(source_image.device)

        return final_image_tensor  
             

    def histogram_matching_withmask(self, source_image, target_image, mask, diffslider):
        # Convert Torch tensors into NumPy arrays and ensure they are on the CPU
        source_image_np = source_image.permute(1, 2, 0).contiguous().detach().cpu().numpy().astype('float32')
        target_image_np = target_image.permute(1, 2, 0).contiguous().detach().cpu().numpy().astype('float32')
        mask_np = mask.squeeze().contiguous().detach().cpu().numpy().astype('float32')

        # Normalize the images to the range [0, 1]
        source_image_np /= 255.0
        target_image_np /= 255.0

        # Apply histogram matching only to the masked areas
        matched_target_image_np = np.copy(target_image_np)

        # Define the condition for the mask
        valid_mask = mask_np > 0.2

        for channel in range(3):
            # Perform calculations only on the valid (non-masked) areas
            masked_source_values = source_image_np[:, :, channel][valid_mask]
            masked_target_values = target_image_np[:, :, channel][valid_mask]

            # Calculate the histograms and cumulative distributions only for the valid areas
            source_hist, bin_edges = np.histogram(masked_source_values, bins=256, range=(0, 1), density=True)
            target_hist, _ = np.histogram(masked_target_values, bins=256, range=(0, 1), density=True)

            source_cdf = np.cumsum(source_hist)
            target_cdf = np.cumsum(target_hist)

            # Normalize the CDFs
            source_cdf /= source_cdf[-1]
            target_cdf /= target_cdf[-1]

            # Create a mapping function from target CDF to source CDF
            interp_t_values = np.interp(target_image_np[:, :, channel].flatten(), bin_edges[:-1], target_cdf)
            matched_channel = np.interp(interp_t_values, source_cdf, bin_edges[:-1])

            # Apply the mapping only to the valid areas
            matched_channel_reshaped = matched_channel.reshape(target_image_np[:, :, channel].shape)
            matched_target_image_np[:, :, channel][valid_mask] = matched_channel_reshaped[valid_mask]

        # Interpolate the result depending on the diffslider
        final_image_np = (1 - diffslider / 100) * target_image_np + (diffslider / 100) * matched_target_image_np

        # Convert back to uint8
        final_image_np = np.clip(final_image_np * 255, 0, 255)

        # Convert the result back into a Torch tensor and onto the original device
        final_image_tensor = torch.from_numpy(final_image_np.transpose(2, 0, 1)).float().to(source_image.device)

        return final_image_tensor 

    def histogram_matching_DFL_test(self, source_image, target_image, diffslider):
        # Convert Torch tensors into CuPy arrays and ensure they are on the GPU
        source_image_np = source_image.cpu().numpy().astype('float32').transpose(1, 2, 0)
        target_image_np = target_image.cpu().numpy().astype('float32').transpose(1, 2, 0)
        # normalize images [0, 1]
        source_image_np /= 255.0
        target_image_np /= 255.0

        source = cv2.cvtColor(source_image_np, cv2.COLOR_BGR2LAB)
        target = cv2.cvtColor(target_image_np, cv2.COLOR_BGR2LAB)

        target_l_mean, target_l_std, target_a_mean, target_a_std, target_b_mean, target_b_std, \
            = target[...,0].mean(), target[...,0].std(), target[...,1].mean(), target[...,1].std(), target[...,2].mean(), target[...,2].std()
        
        source_l_mean, source_l_std, source_a_mean, source_a_std, source_b_mean, source_b_std, \
            = source[...,0].mean(), source[...,0].std(), source[...,1].mean(), source[...,1].std(), source[...,2].mean(), source[...,2].std()       
        
        # not as in the paper: scale by the standard deviations using reciprocal of paper proposed factor
        target_l = target[...,0]
        target_l = ne.evaluate('(target_l - target_l_mean) * source_l_std / target_l_std + source_l_mean')

        target_a = target[...,1]
        target_a = ne.evaluate('(target_a - target_a_mean) * source_a_std / target_a_std + source_a_mean')
        
        target_b = target[...,2]
        target_b = ne.evaluate('(target_b - target_b_mean) * source_b_std / target_b_std + source_b_mean')
        
        np.clip(target_l,    0, 100, out=target_l)
        np.clip(target_a, -127, 127, out=target_a)
        np.clip(target_b, -127, 127, out=target_b)
        
        matched_target_image_np = cv2.cvtColor(np.stack([target_l,target_a,target_b], -1), cv2.COLOR_LAB2BGR)
        final_image_np = (1 - diffslider/100) * target_image_np + diffslider/100 * matched_target_image_np
        final_image_np = np.clip(final_image_np * 255, 0, 255)#.astype(np.uint8)        
        final_image_tensor = torch.from_numpy(final_image_np.transpose(2, 0, 1)).float().to(source_image.device)

        return final_image_tensor        
        
    def histogram_matching_DFL_Orig(self, source_image, target_image, mask, diffslider):        
        # Convert Torch tensors into CuPy arrays and ensure they are on the GPU
        source_image_np = source_image.cpu().numpy().astype('float32').transpose(1, 2, 0)
        target_image_np = target_image.cpu().numpy().astype('float32').transpose(1, 2, 0)
        mask_np = mask.cpu().numpy().astype('float32').transpose(1, 2, 0) 
        mask_cutoff = 0.2

        source_mask = mask_np
        target_mask = mask_np        
        
        # normalize images [0, 1]
        source_image_np /= 255.0
        target_image_np /= 255.0

        source = cv2.cvtColor(source_image_np, cv2.COLOR_BGR2LAB)
        target = cv2.cvtColor(target_image_np, cv2.COLOR_BGR2LAB)

        source_input = source
        if source_mask is not None:
            source_input = source_input.copy()
            source_input[source_mask[...,0] < mask_cutoff] = [0,0,0]
        
        target_input = target
        if target_mask is not None:
            target_input = target_input.copy()
            target_input[target_mask[...,0] < mask_cutoff] = [0,0,0]
            

        target_l_mean, target_l_std, target_a_mean, target_a_std, target_b_mean, target_b_std, \
            = target_input[...,0].mean(), target_input[...,0].std(), target_input[...,1].mean(), target_input[...,1].std(), target_input[...,2].mean(), target_input[...,2].std()
        
        source_l_mean, source_l_std, source_a_mean, source_a_std, source_b_mean, source_b_std, \
            = source_input[...,0].mean(), source_input[...,0].std(), source_input[...,1].mean(), source_input[...,1].std(), source_input[...,2].mean(), source_input[...,2].std()       
        
        # not as in the paper: scale by the standard deviations using reciprocal of paper proposed factor
        target_l = target[...,0]
        target_l = ne.evaluate('(target_l - target_l_mean) * source_l_std / target_l_std + source_l_mean')

        target_a = target[...,1]
        target_a = ne.evaluate('(target_a - target_a_mean) * source_a_std / target_a_std + source_a_mean')
        
        target_b = target[...,2]
        target_b = ne.evaluate('(target_b - target_b_mean) * source_b_std / target_b_std + source_b_mean')      

        np.clip(target_l,    0, 100, out=target_l)
        np.clip(target_a, -127, 127, out=target_a)
        np.clip(target_b, -127, 127, out=target_b)
        
        matched_target_image_np = cv2.cvtColor(np.stack([target_l,target_a,target_b], -1), cv2.COLOR_LAB2BGR)
        final_image_np = (1 - diffslider/100) * target_image_np + diffslider/100 * matched_target_image_np
        final_image_np = np.clip(final_image_np * 255, 0, 255)#.astype(np.uint8)
        
        final_image_tensor = torch.from_numpy(final_image_np.transpose(2, 0, 1)).float().to(source_image.device)
 
        return final_image_tensor        

    def swap_edit_face_core(self, img, kps, parameters, parameters_face_editor, control, **kwargs): # img = RGB
        # Scaling Transforms
        t256 = v2.Resize((256, 256), interpolation=v2.InterpolationMode.BILINEAR, antialias=False)

        # initial eye_ratio and lip_ratio values
        init_source_eye_ratio = 0.0
        init_source_lip_ratio = 0.0

        # Grab 512 face from image and create 256 and 128 copys
        if parameters_face_editor["FaceEditorTypeTextSel"] == "Human-Face":
            _, lmk_crop, _ = self.models.run_detect_landmark( img, bbox=[], det_kpss=kps, detect_mode='203', score=0.5, from_points=True)
            source_eye_ratio = faceutil.calc_eye_close_ratio(lmk_crop[None])
            source_lip_ratio = faceutil.calc_lip_close_ratio(lmk_crop[None])
            init_source_eye_ratio = round(float(source_eye_ratio.mean()), 2)
            init_source_lip_ratio = round(float(source_lip_ratio[0][0]), 2)

            # prepare_retargeting_image
            original_face_512, M_o2c, M_c2o = faceutil.warp_face_by_face_landmark_x(img, kps, dsize=512, scale=parameters_face_editor["CropScaleSlider"], vy_ratio=-0.125, interpolation=v2.InterpolationMode.BILINEAR)
            original_face_256 = t256(original_face_512)
            mask_ori = faceutil.prepare_paste_back(self.models.lp_mask_crop, M_c2o, dsize=(img.shape[1], img.shape[2])).contiguous()

            x_s_info = self.models.lp_motion_extractor(original_face_256, 'Human-Face')
            x_d_info_user_pitch = x_s_info['pitch'] + parameters_face_editor['HeadPitchSlider'] #input_head_pitch_variation
            x_d_info_user_yaw = x_s_info['yaw'] + parameters_face_editor['HeadYawSlider'] # input_head_yaw_variation
            x_d_info_user_roll = x_s_info['roll'] + parameters_face_editor['HeadRollSlider'] #input_head_roll_variation
            R_s_user = faceutil.get_rotation_matrix(x_s_info['pitch'], x_s_info['yaw'], x_s_info['roll'])
            R_d_user = faceutil.get_rotation_matrix(x_d_info_user_pitch, x_d_info_user_yaw, x_d_info_user_roll)
            f_s_user = self.models.lp_appearance_feature_extractor(original_face_256, 'Human-Face')
            x_s_user = faceutil.transform_keypoint(x_s_info)

            #execute_image_retargeting
            mov_x = torch.tensor(parameters_face_editor['XAxisMovementSlider']).to(device)
            mov_y = torch.tensor(parameters_face_editor['YAxisMovementSlider']).to(device)
            mov_z = torch.tensor(parameters_face_editor['ZAxisMovementSlider']).to(device)
            eyeball_direction_x = torch.tensor(parameters_face_editor['EyeGazeHorizontalSlider']).to(device)
            eyeball_direction_y = torch.tensor(parameters_face_editor['EyeGazeVerticalSlider']).to(device)
            smile = torch.tensor(parameters_face_editor['MouthSmileSlider']).to(device)
            wink = torch.tensor(parameters_face_editor['EyeWinkSlider']).to(device)
            eyebrow = torch.tensor(parameters_face_editor['EyeBrowsDirectionSlider']).to(device)
            lip_variation_zero = torch.tensor(parameters_face_editor['MouthPoutingSlider']).to(device)
            lip_variation_one = torch.tensor(parameters_face_editor['MouthPursingSlider']).to(device)
            lip_variation_two = torch.tensor(parameters_face_editor['MouthGrinSlider']).to(device)
            lip_variation_three = torch.tensor(parameters_face_editor['LipsCloseOpenSlider']).to(device)

            x_c_s = x_s_info['kp']
            delta_new = x_s_info['exp']
            scale_new = x_s_info['scale']
            t_new = x_s_info['t']
            R_d_new = (R_d_user @ R_s_user.permute(0, 2, 1)) @ R_s_user

            if eyeball_direction_x != 0 or eyeball_direction_y != 0:
                delta_new = faceutil.update_delta_new_eyeball_direction(eyeball_direction_x, eyeball_direction_y, delta_new)
            if smile != 0:
                delta_new = faceutil.update_delta_new_smile(smile, delta_new)
            if wink != 0:
                delta_new = faceutil.update_delta_new_wink(wink, delta_new)
            if eyebrow != 0:
                delta_new = faceutil.update_delta_new_eyebrow(eyebrow, delta_new)
            if lip_variation_zero != 0:
                delta_new = faceutil.update_delta_new_lip_variation_zero(lip_variation_zero, delta_new)
            if lip_variation_one !=  0:
                delta_new = faceutil.update_delta_new_lip_variation_one(lip_variation_one, delta_new)
            if lip_variation_two != 0:
                delta_new = faceutil.update_delta_new_lip_variation_two(lip_variation_two, delta_new)
            if lip_variation_three != 0:
                delta_new = faceutil.update_delta_new_lip_variation_three(lip_variation_three, delta_new)
            if mov_x != 0:
                delta_new = faceutil.update_delta_new_mov_x(-mov_x, delta_new)
            if mov_y !=0 :
                delta_new = faceutil.update_delta_new_mov_y(mov_y, delta_new)

            x_d_new = mov_z * scale_new * (x_c_s @ R_d_new + delta_new) + t_new
            eyes_delta, lip_delta = None, None

            input_eye_ratio = max(min(init_source_eye_ratio + parameters_face_editor['EyesOpenRatioSlider'], 0.80), 0.00)
            if input_eye_ratio != init_source_eye_ratio:
                combined_eye_ratio_tensor = faceutil.calc_combined_eye_ratio([[float(input_eye_ratio)]], lmk_crop)
                eyes_delta = self.models.lp_retarget_eye(x_s_user, combined_eye_ratio_tensor, 'Human-Face')

            input_lip_ratio = max(min(init_source_lip_ratio + parameters_face_editor['LipsOpenRatioSlider'], 0.80), 0.00)
            if input_lip_ratio != init_source_lip_ratio:
                combined_lip_ratio_tensor = faceutil.calc_combined_lip_ratio([[float(input_lip_ratio)]], lmk_crop)
                lip_delta = self.models.lp_retarget_lip(x_s_user, combined_lip_ratio_tensor)

            x_d_new = x_d_new + \
                    (eyes_delta if eyes_delta is not None else 0) + \
                    (lip_delta if lip_delta is not None else 0)

            flag_stitching_retargeting_input: bool = kwargs.get('flag_stitching_retargeting_input', True)
            if flag_stitching_retargeting_input:
                x_d_new = self.models.lp_stitching(x_s_user, x_d_new)

            out = self.models.lp_warp_decode(f_s_user, x_s_user, x_d_new, 'Human-Face')
            out = torch.squeeze(out)
            out = torch.clamp(out, 0, 1)  # clip to 0~1
            out = torch.clamp(torch.mul(out, 255), 0, 255).type(torch.uint8)  # 0~1 -> 0~255

            flag_do_crop_input_retargeting_image = kwargs.get('flag_do_crop_input_retargeting_image', True)
            if flag_do_crop_input_retargeting_image:
                gauss = transforms.GaussianBlur(parameters['DFLXSegSlider']*2+1, (parameters['DFLXSegSlider']+1)*0.2)
                mask_ori = gauss(mask_ori)
                img = faceutil.paste_back(out, M_c2o, img, mask_ori)
            else:
                img = out

        return img

    # @profile
    def apply_occlusion(self, img, amount):
        img = torch.div(img, 255)
        img = torch.unsqueeze(img, 0).contiguous()
        outpred = torch.ones((256,256), dtype=torch.float32, device=device).contiguous()

        self.models.run_occluder(img, outpred)

        outpred = torch.squeeze(outpred)
        outpred = (outpred > 0)
        outpred = torch.unsqueeze(outpred, 0).type(torch.float32)

        if amount >0:
            kernel = torch.ones((1,1,3,3), dtype=torch.float32, device=device)

            for i in range(int(amount)):
                outpred = torch.nn.functional.conv2d(outpred, kernel, padding=(1, 1))
                outpred = torch.clamp(outpred, 0, 1)

            outpred = torch.squeeze(outpred)

        if amount <0:
            outpred = torch.neg(outpred)
            outpred = torch.add(outpred, 1)
            kernel = torch.ones((1,1,3,3), dtype=torch.float32, device=device)

            for i in range(int(-amount)):
                outpred = torch.nn.functional.conv2d(outpred, kernel, padding=(1, 1))
                outpred = torch.clamp(outpred, 0, 1)

            outpred = torch.squeeze(outpred)
            outpred = torch.neg(outpred)
            outpred = torch.add(outpred, 1)

        outpred = torch.reshape(outpred, (1, 256, 256))
        return outpred

    def apply_dfl_xseg(self, img, amount):
        img = img.type(torch.float32)
        img = torch.div(img, 255)
        img = torch.unsqueeze(img, 0).contiguous()
        outpred = torch.ones((256,256), dtype=torch.float32, device=device).contiguous()

        self.models.run_dfl_xseg(img, outpred)

        outpred = torch.clamp(outpred, min=0.0, max=1.0)
        outpred[outpred < 0.1] = 0
        # invert values to mask areas to keep
        outpred = 1.0 - outpred
        outpred = torch.unsqueeze(outpred, 0).type(torch.float32)

        if amount > 0:
            kernel = torch.ones((1,1,3,3), dtype=torch.float32, device=device)

            for i in range(int(amount)):
                outpred = torch.nn.functional.conv2d(outpred, kernel, padding=(1, 1))
                outpred = torch.clamp(outpred, 0, 1)

            outpred = torch.squeeze(outpred)

        if amount < 0:
            outpred = torch.neg(outpred)
            outpred = torch.add(outpred, 1)
            kernel = torch.ones((1,1,3,3), dtype=torch.float32, device=device)

            for i in range(int(-amount)):
                outpred = torch.nn.functional.conv2d(outpred, kernel, padding=(1, 1))
                outpred = torch.clamp(outpred, 0, 1)

            outpred = torch.squeeze(outpred)
            outpred = torch.neg(outpred)
            outpred = torch.add(outpred, 1)

        outpred = torch.reshape(outpred, (1, 256, 256))
        return outpred

    def apply_CLIPs(self, img, CLIPText, CLIPAmount):
        clip_mask = np.ones((352, 352))
        img = img.permute(1,2,0)
        img = img.cpu().numpy()
        # img = img.to(torch.float)
        # img = img.permute(1,2,0)
        transform = transforms.Compose([transforms.ToTensor(),
                                        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
                                        transforms.Resize((352, 352))])
        CLIPimg = transform(img).unsqueeze(0).contiguous()

        if CLIPText != "":
            prompts = CLIPText.split(',')

            with torch.no_grad():
                preds = self.clip_session(CLIPimg.repeat(len(prompts),1,1,1), prompts)[0]
                # preds = self.clip_session(CLIPimg,  maskimg, True)[0]

            clip_mask = 1 - torch.sigmoid(preds[0][0])
            for i in range(len(prompts)-1):
                clip_mask *= 1-torch.sigmoid(preds[i+1][0])
            clip_mask = clip_mask.data.cpu().numpy()

            thresh = CLIPAmount/100.0
            clip_mask[clip_mask>thresh] = 1.0
            clip_mask[clip_mask<=thresh] = 0.0
        return clip_mask

    # @profile
    def apply_face_parser(self, img, parameters):
        # atts = [1 'skin', 2 'l_brow', 3 'r_brow', 4 'l_eye', 5 'r_eye', 6 'eye_g', 7 'l_ear', 8 'r_ear', 9 'ear_r', 10 'nose', 11 'mouth', 12 'u_lip', 13 'l_lip', 14 'neck', 15 'neck_l', 16 'cloth', 17 'hair', 18 'hat']
        FaceAmount = parameters["FaceParserSlider"]

        img = torch.div(img, 255)
        img = v2.functional.normalize(img, (0.485, 0.456, 0.406), (0.229, 0.224, 0.225))
        img = torch.reshape(img, (1, 3, 512, 512))
        outpred = torch.empty((1,19,512,512), dtype=torch.float32, device='cuda').contiguous()

        self.models.run_faceparser(img, outpred)

        outpred = torch.squeeze(outpred)
        outpred = torch.argmax(outpred, 0)

        face_attributes = {
            2: parameters['LeftEyeBrowParserSlider'], #Left Eyebrow
            3: parameters['RightEyeBrowParserSlider'], #Right Eyebrow
            4: parameters['LeftEyeParserSlider'], #Left Eye
            5: parameters['RightEyeParserSlider'], #Right Eye
            10: parameters['NoseParserSlider'], #Nose
            11: parameters['MouthParserSlider'], #Mouth
            12: parameters['UpperLipParserSlider'], #Upper Lip
            13: parameters['LowerLipParserSlider'], #Lower Lip
            14: parameters['NeckParserSlider'], #Neck
        }
        face_parses = []
        for attribute in face_attributes.keys():
            if face_attributes[attribute] > 0:
                attribute_idxs = torch.tensor( [attribute], device='cuda')
                iters = int(face_attributes[attribute])

                attribute_parse = torch.isin(outpred, attribute_idxs)
                attribute_parse = torch.clamp(~attribute_parse, 0, 1).type(torch.float32)
                attribute_parse = torch.reshape(attribute_parse, (1,1,512,512))
                attribute_parse = torch.neg(attribute_parse)
                attribute_parse = torch.add(attribute_parse, 1)

                kernel = torch.ones((1,1,3,3), dtype=torch.float32, device='cuda')

                for i in range(iters):
                    attribute_parse = torch.nn.functional.conv2d(attribute_parse, kernel, padding=(1, 1))
                    attribute_parse = torch.clamp(attribute_parse, 0, 1)

                attribute_parse = torch.squeeze(attribute_parse)
                attribute_parse = torch.neg(attribute_parse)
                attribute_parse = torch.add(attribute_parse, 1)
                attribute_parse = torch.reshape(attribute_parse, (1, 512, 512))
                gauss = transforms.GaussianBlur(parameters['ParserBlurSlider']*2+1, (parameters['ParserBlurSlider']+1)*0.2)
                attribute_parse = gauss(attribute_parse)
            else:
                attribute_parse = torch.ones((1, 512, 512), dtype=torch.float32, device='cuda')
            face_parses.append(attribute_parse)

        # BG Parse
        bg_idxs = torch.tensor([0, 14, 15, 16, 17, 18], device=device)
        bg_parse = torch.isin(outpred, bg_idxs)
        bg_parse = torch.clamp(~bg_parse, 0, 1).type(torch.float32)
        bg_parse = torch.reshape(bg_parse, (1, 1, 512, 512))

        if FaceAmount > 0:
            kernel = torch.ones((1, 1, 3, 3), dtype=torch.float32, device=device)

            for i in range(int(FaceAmount)):
                bg_parse = torch.nn.functional.conv2d(bg_parse, kernel, padding=(1, 1))
                bg_parse = torch.clamp(bg_parse, 0, 1)
            gauss = transforms.GaussianBlur(parameters['BGParserBlurSlider']*2+1, (parameters['BGParserBlurSlider']+1)*0.2)
            bg_parse = gauss(bg_parse)
            bg_parse = torch.squeeze(bg_parse)

        elif FaceAmount < 0:
            bg_parse = torch.neg(bg_parse)
            bg_parse = torch.add(bg_parse, 1)

            kernel = torch.ones((1, 1, 3, 3), dtype=torch.float32, device=device)

            for i in range(int(-FaceAmount)):
                bg_parse = torch.nn.functional.conv2d(bg_parse, kernel, padding=(1, 1))
                bg_parse = torch.clamp(bg_parse, 0, 1)

            bg_parse = torch.squeeze(bg_parse)
            bg_parse = torch.neg(bg_parse)
            bg_parse = torch.add(bg_parse, 1)
            bg_parse = torch.reshape(bg_parse, (1, 512, 512))
            gauss = transforms.GaussianBlur(parameters['BGParserBlurSlider']*2+1, (parameters['BGParserBlurSlider']+1)*0.2)
            bg_parse = gauss(bg_parse)
        else:
            bg_parse = torch.ones((1,512,512), dtype=torch.float32, device='cuda')

        out_parse = bg_parse
        for face_parse in face_parses:
            out_parse = torch.mul(out_parse, face_parse)

        return out_parse

    def apply_restorer(self, swapped_face_upscaled, parameters):
        temp = swapped_face_upscaled
        t512 = v2.Resize((512, 512), antialias=False)
        t256 = v2.Resize((256, 256), antialias=False)
        t1024 = v2.Resize((1024, 1024), antialias=False)
        t2048 = v2.Resize((2048, 2048), antialias=False)

        # If using a separate detection mode
        if parameters['RestorerDetTypeTextSel'] == 'Blend' or parameters['RestorerDetTypeTextSel'] == 'Reference':
            if parameters['RestorerDetTypeTextSel'] == 'Blend':
                # Set up Transformation
                dst = self.arcface_dst * 4.0
                dst[:,0] += 32.0

            elif parameters['RestorerDetTypeTextSel'] == 'Reference':
                try:
                    dst = self.models.resnet50(swapped_face_upscaled, score=parameters['DetectScoreSlider']/100.0)
                except Exception as e:
                    print(f"exception: {e}")
                    return swapped_face_upscaled

            tform = trans.SimilarityTransform()
            tform.estimate(dst, self.FFHQ_kps)

            # Transform, scale, and normalize
            temp = v2.functional.affine(swapped_face_upscaled, tform.rotation*57.2958, (tform.translation[0], tform.translation[1]) , tform.scale, 0, center = (0,0) )
            temp = v2.functional.crop(temp, 0,0, 512, 512)

        temp = torch.div(temp, 255)
        temp = v2.functional.normalize(temp, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=False)

        if parameters['RestorerTypeTextSel'] == 'GPEN-256':
            temp = t256(temp)

        temp = torch.unsqueeze(temp, 0).contiguous()

        # Bindings
        outpred = torch.empty((1,3,512,512), dtype=torch.float32, device=device).contiguous()

        if parameters['RestorerTypeTextSel'] == 'GFPGAN-v1.4':
            self.models.run_GFPGAN(temp, outpred)

        elif parameters['RestorerTypeTextSel'] == 'CodeFormer':
            self.models.run_codeformer(temp, outpred, parameters['VQFRFidelitySlider'])

        elif parameters['RestorerTypeTextSel'] == 'GPEN-256':
            outpred = torch.empty((1,3,256,256), dtype=torch.float32, device=device).contiguous()
            self.models.run_GPEN_256(temp, outpred)

        elif parameters['RestorerTypeTextSel'] == 'GPEN-512':
            self.models.run_GPEN_512(temp, outpred)

        elif parameters['RestorerTypeTextSel'] == 'GPEN-1024':
            temp = t1024(temp)
            outpred = torch.empty((1, 3, 1024, 1024), dtype=torch.float32, device=device).contiguous()
            self.models.run_GPEN_1024(temp, outpred)

        elif parameters['RestorerTypeTextSel'] == 'GPEN-2048':
            temp = t2048(temp)
            outpred = torch.empty((1, 3, 2048, 2048), dtype=torch.float32, device=device).contiguous()
            self.models.run_GPEN_2048(temp, outpred)

        elif parameters['RestorerTypeTextSel'] == 'RestoreFormer++':
            self.models.run_RestoreFormerPlusPlus(temp, outpred)

        elif parameters['RestorerTypeTextSel'] == 'VQFR-v2':
            self.models.run_VQFR_v2(temp, outpred, parameters['VQFRFidelitySlider'])

        # Format back to cxHxW @ 255
        outpred = torch.squeeze(outpred)
        outpred = torch.clamp(outpred, -1, 1)
        outpred = torch.add(outpred, 1)
        outpred = torch.div(outpred, 2)
        outpred = torch.mul(outpred, 255)

        if parameters['RestorerTypeTextSel'] == 'GPEN-256' or parameters['RestorerTypeTextSel'] == 'GPEN-1024' or parameters['RestorerTypeTextSel'] == 'GPEN-2048':
            outpred = t512(outpred)

        # Invert Transform
        if parameters['RestorerDetTypeTextSel'] == 'Blend' or parameters['RestorerDetTypeTextSel'] == 'Reference':
            outpred = v2.functional.affine(outpred, tform.inverse.rotation*57.2958, (tform.inverse.translation[0], tform.inverse.translation[1]), tform.inverse.scale, 0, interpolation=v2.InterpolationMode.BILINEAR, center = (0,0) )

        # Blend
        alpha = float(parameters["RestorerSlider"])/100.0
        outpred = torch.add(torch.mul(outpred, alpha), torch.mul(swapped_face_upscaled, 1-alpha))

        return outpred
        
    def apply_restorer2(self, swapped_face_upscaled, parameters):
        temp = swapped_face_upscaled
        t512 = v2.Resize((512, 512), antialias=False)
        t256 = v2.Resize((256, 256), antialias=False)
        t1024 = v2.Resize((1024, 1024), antialias=False)
        t2048 = v2.Resize((2048, 2048), antialias=False)

        # If using a separate detection mode
        if parameters['Restorer2DetTypeTextSel'] == 'Blend' or parameters['Restorer2DetTypeTextSel'] == 'Reference':
            if parameters['Restorer2DetTypeTextSel'] == 'Blend':
                # Set up Transformation
                dst = self.arcface_dst * 4.0
                dst[:,0] += 32.0

            elif parameters['Restorer2DetTypeTextSel'] == 'Reference':
                try:
                    dst = self.models.resnet50(swapped_face_upscaled, score=parameters['DetectScoreSlider']/100.0)
                except Exception as e:
                    print(f"exception: {e}")
                    return swapped_face_upscaled

            tform = trans.SimilarityTransform()
            tform.estimate(dst, self.FFHQ_kps)

            # Transform, scale, and normalize
            temp = v2.functional.affine(swapped_face_upscaled, tform.rotation*57.2958, (tform.translation[0], tform.translation[1]) , tform.scale, 0, center = (0,0) )
            temp = v2.functional.crop(temp, 0,0, 512, 512)

        temp = torch.div(temp, 255)
        temp = v2.functional.normalize(temp, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=False)

        if parameters['Restorer2TypeTextSel'] == 'GPEN-256':
            temp = t256(temp)

        temp = torch.unsqueeze(temp, 0).contiguous()

        # Bindings
        outpred = torch.empty((1,3,512,512), dtype=torch.float32, device=device).contiguous()

        if parameters['Restorer2TypeTextSel'] == 'GFPGAN-v1.4':
            self.models.run_GFPGAN(temp, outpred)

        elif parameters['Restorer2TypeTextSel'] == 'CodeFormer':
            self.models.run_codeformer(temp, outpred, parameters['VQFRFidelitySlider'])

        elif parameters['Restorer2TypeTextSel'] == 'GPEN-256':
            outpred = torch.empty((1,3,256,256), dtype=torch.float32, device=device).contiguous()
            self.models.run_GPEN_256(temp, outpred)

        elif parameters['Restorer2TypeTextSel'] == 'GPEN-512':
            self.models.run_GPEN_512(temp, outpred)

        elif parameters['Restorer2TypeTextSel'] == 'GPEN-1024':
            temp = t1024(temp)
            outpred = torch.empty((1, 3, 1024, 1024), dtype=torch.float32, device=device).contiguous()
            self.models.run_GPEN_1024(temp, outpred)

        elif parameters['Restorer2TypeTextSel'] == 'GPEN-2048':
            temp = t2048(temp)
            outpred = torch.empty((1, 3, 2048, 2048), dtype=torch.float32, device=device).contiguous()
            self.models.run_GPEN_2048(temp, outpred)

        elif parameters['Restorer2TypeTextSel'] == 'RestoreFormer++':
            self.models.run_RestoreFormerPlusPlus(temp, outpred)

        elif parameters['Restorer2TypeTextSel'] == 'VQFR-v2':
            self.models.run_VQFR_v2(temp, outpred, parameters['VQFRFidelitySlider'])

        # Format back to cxHxW @ 255
        outpred = torch.squeeze(outpred)
        outpred = torch.clamp(outpred, -1, 1)
        outpred = torch.add(outpred, 1)
        outpred = torch.div(outpred, 2)
        outpred = torch.mul(outpred, 255)

        if parameters['Restorer2TypeTextSel'] == 'GPEN-256' or parameters['Restorer2TypeTextSel'] == 'GPEN-1024' or parameters['Restorer2TypeTextSel'] == 'GPEN-2048':
            outpred = t512(outpred)

        # Invert Transform
        if parameters['Restorer2DetTypeTextSel'] == 'Blend' or parameters['Restorer2DetTypeTextSel'] == 'Reference':
            outpred = v2.functional.affine(outpred, tform.inverse.rotation*57.2958, (tform.inverse.translation[0], tform.inverse.translation[1]), tform.inverse.scale, 0, interpolation=v2.InterpolationMode.BILINEAR, center = (0,0) )

        # Blend
        alpha = float(parameters["Restorer2Slider"])/100.0
        outpred = torch.add(torch.mul(outpred, alpha), torch.mul(swapped_face_upscaled, 1-alpha))

        return outpred        

    def apply_fake_diff(self, swapped_face, original_face, DiffAmount):
        swapped_face = swapped_face.permute(1,2,0)
        original_face = original_face.permute(1,2,0)

        diff = swapped_face-original_face
        diff = torch.abs(diff)

        # Find the diffrence between the swap and original, per channel
        fthresh = DiffAmount*2.55

        # Bimodal
        diff[diff<fthresh] = 0
        diff[diff>=fthresh] = 1

        # If any of the channels exceeded the threshhold, them add them to the mask
        diff = torch.sum(diff, dim=2)
        diff = torch.unsqueeze(diff, 2)
        diff[diff>0] = 1

        diff = diff.permute(2,0,1)

        return diff

    def soft_oval_mask(self, height, width, center, radius_x, radius_y, feather_radius=None):
        """
        Create a soft oval mask with feathering effect using integer operations.

        Args:
            height (int): Height of the mask.
            width (int): Width of the mask.
            center (tuple): Center of the oval (x, y).
            radius_x (int): Radius of the oval along the x-axis.
            radius_y (int): Radius of the oval along the y-axis.
            feather_radius (int): Radius for feathering effect.

        Returns:
            torch.Tensor: Soft oval mask tensor of shape (H, W).
        """
        if feather_radius is None:
            feather_radius = max(radius_x, radius_y) // 2  # Integer division

        # Calculating the normalized distance from the center
        y, x = torch.meshgrid(torch.arange(height), torch.arange(width), indexing='ij')

        # Calculating the normalized distance from the center
        normalized_distance = torch.sqrt(((x - center[0]) / radius_x) ** 2 + ((y - center[1]) / radius_y) ** 2)

        # Creating the oval mask with a feathering effect
        mask = torch.clamp((1 - normalized_distance) * (radius_x / feather_radius), 0, 1)

        return mask

    def restore_mouth(self, img_orig, img_swap, kpss_orig, ParserBlurSlider, blend_alpha=0.5, feather_radius=10, size_factor=0.5, radius_factor_x=1.0, radius_factor_y=1.0):
        """
        Extract mouth from img_orig using the provided keypoints and place it in img_swap.

        Args:
            img_orig (torch.Tensor): The original image tensor of shape (C, H, W) from which mouth is extracted.
            img_swap (torch.Tensor): The target image tensor of shape (C, H, W) where mouth is placed.
            kpss_orig (list): List of keypoints arrays for detected faces. Each keypoints array contains coordinates for 5 keypoints.
            radius_factor_x (float): Factor to scale the horizontal radius. 1.0 means circular, >1.0 means wider oval, <1.0 means narrower.
            radius_factor_y (float): Factor to scale the vertical radius. 1.0 means circular, >1.0 means taller oval, <1.0 means shorter.

        Returns:
            torch.Tensor: The resulting image tensor with mouth from img_orig placed on img_swap.
        """
        left_mouth = np.array([int(val) for val in kpss_orig[3]])
        right_mouth = np.array([int(val) for val in kpss_orig[4]])

        mouth_center = (left_mouth + right_mouth) // 2
        mouth_base_radius = int(np.linalg.norm(left_mouth - right_mouth) * size_factor)

        # Calculate the scaled radii
        radius_x = int(mouth_base_radius * radius_factor_x)
        radius_y = int(mouth_base_radius * radius_factor_y)

        ymin = max(0, mouth_center[1] - radius_y)
        ymax = min(img_orig.size(1), mouth_center[1] + radius_y)
        xmin = max(0, mouth_center[0] - radius_x)
        xmax = min(img_orig.size(2), mouth_center[0] + radius_x)

        mouth_region_orig = img_orig[:, ymin:ymax, xmin:xmax]
        mouth_mask = self.soft_oval_mask(ymax - ymin, xmax - xmin,
                                         (radius_x, radius_y),
                                         radius_x, radius_y,
                                         feather_radius).to(img_orig.device)

        target_ymin = ymin
        target_ymax = ymin + mouth_region_orig.size(1)
        target_xmin = xmin
        target_xmax = xmin + mouth_region_orig.size(2)

        img_swap_mouth = img_swap[:, target_ymin:target_ymax, target_xmin:target_xmax]
        blended_mouth = blend_alpha * img_swap_mouth + (1 - blend_alpha) * mouth_region_orig
        
        # Schritt 1: Maske in 3D-Form bringen (1, 44, 60), indem eine Kanal-Dimension hinzugefügt wird
        #mask_3d = mouth_mask.unsqueeze(0)  # Fügt die Kanal-Dimension hinzu, jetzt (1, 44, 60)

        # Schritt 2: Gaussian Blur anwenden
        # kernel_size muss eine ungerade Zahl sein (z.B. 5), sigma ist der Standardabweichungswert
        #gauss = transforms.GaussianBlur(ParserBlurSlider*2+1, (ParserBlurSlider+1)*0.2)
        #mouth_mask = gauss(mask_3d)
        # Schritt 3: Zurück zur 2D-Form (44, 60), indem die Kanal-Dimension entfernt wird
        #mouth_mask = mouth_mask.squeeze(0)  # Entfernt die Kanal-Dimension

        img_swap[:, target_ymin:target_ymax, target_xmin:target_xmax] = mouth_mask * blended_mouth + (1 - mouth_mask) * img_swap_mouth
        return img_swap

    def restore_eyes(self, img_orig, img_swap, kpss_orig, ParserBlurSlider, blend_alpha=0.5, feather_radius=10, size_factor=3.5, radius_factor_x=1.0, radius_factor_y=1.0):
        """
        Extract eyes from img_orig using the provided keypoints and place them in img_swap.

        Args:
            img_orig (torch.Tensor): The original image tensor of shape (C, H, W) from which eyes are extracted.
            img_swap (torch.Tensor): The target image tensor of shape (C, H, W) where eyes are placed.
            kpss_orig (list): List of keypoints arrays for detected faces. Each keypoints array contains coordinates for 5 keypoints.
            radius_factor_x (float): Factor to scale the horizontal radius. 1.0 means circular, >1.0 means wider oval, <1.0 means narrower.
            radius_factor_y (float): Factor to scale the vertical radius. 1.0 means circular, >1.0 means taller oval, <1.0 means shorter.

        Returns:
            torch.Tensor: The resulting image tensor with eyes from img_orig placed on img_swap.
        """
        left_eye = np.array([int(val) for val in kpss_orig[0]])
        right_eye = np.array([int(val) for val in kpss_orig[1]])

        eye_distance = np.linalg.norm(left_eye - right_eye)
        base_eye_radius = int(eye_distance / size_factor)

        # Calculate the scaled radii
        radius_x = int(base_eye_radius * radius_factor_x)
        radius_y = int(base_eye_radius * radius_factor_y)

        def extract_and_blend_eye(eye_center, radius_x, radius_y, img_orig, img_swap, blend_alpha, feather_radius):
            ymin = max(0, eye_center[1] - radius_y)
            ymax = min(img_orig.size(1), eye_center[1] + radius_y)
            xmin = max(0, eye_center[0] - radius_x)
            xmax = min(img_orig.size(2), eye_center[0] + radius_x)

            eye_region_orig = img_orig[:, ymin:ymax, xmin:xmax]
            eye_mask = self.soft_oval_mask(ymax - ymin, xmax - xmin,
                                           (radius_x, radius_y),
                                           radius_x, radius_y,
                                           feather_radius).to(img_orig.device)

            target_ymin = ymin
            target_ymax = ymin + eye_region_orig.size(1)
            target_xmin = xmin
            target_xmax = xmin + eye_region_orig.size(2)

            img_swap_eye = img_swap[:, target_ymin:target_ymax, target_xmin:target_xmax]
            blended_eye = blend_alpha * img_swap_eye + (1 - blend_alpha) * eye_region_orig
            
            # Schritt 1: Maske in 3D-Form bringen (1, 44, 60), indem eine Kanal-Dimension hinzugefügt wird
            #mask_3d = eye_mask.unsqueeze(0)  # Fügt die Kanal-Dimension hinzu, jetzt (1, 44, 60)

            # Schritt 2: Gaussian Blur anwenden
            # kernel_size muss eine ungerade Zahl sein (z.B. 5), sigma ist der Standardabweichungswert
            #gauss = transforms.GaussianBlur(ParserBlurSlider*2+1, (ParserBlurSlider+1)*0.2)
            #eye_mask = gauss(mask_3d)
            # Schritt 3: Zurück zur 2D-Form (44, 60), indem die Kanal-Dimension entfernt wird
            #eye_mask = eye_mask.squeeze(0)  # Entfernt die Kanal-Dimension

                
            img_swap[:, target_ymin:target_ymax, target_xmin:target_xmax] = eye_mask * blended_eye + (1 - eye_mask) * img_swap_eye

        # Process both eyes
        extract_and_blend_eye(left_eye, radius_x, radius_y, img_orig, img_swap, blend_alpha, feather_radius)
        extract_and_blend_eye(right_eye, radius_x, radius_y, img_orig, img_swap, blend_alpha, feather_radius)

        return img_swap
