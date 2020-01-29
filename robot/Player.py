# -*- coding: utf-8-*-
import subprocess
import os
import platform
import time
import json
from . import utils, config
import _thread as thread
from robot import logging
from ctypes import CFUNCTYPE, c_char_p, c_int, cdll
from contextlib import contextmanager

logger = logging.getLogger(__name__)

def py_error_handler(filename, line, function, err, fmt):
    pass

ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)

c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)

@contextmanager
def no_alsa_error():
    try:
        asound = cdll.LoadLibrary('libasound.so')
        asound.snd_lib_error_set_handler(c_error_handler)
        yield
        asound.snd_lib_error_set_handler(None)
    except:
        yield
        pass

def play(fname, onCompleted=None, wait=False):
    player = getPlayerByFileName(fname)
    player.play(fname, onCompleted=onCompleted, wait=wait)

def getPlayerByFileName(fname):
    foo, ext = os.path.splitext(fname)
    if ext in ['.mp3', '.wav']:
        return SoxPlayer()

class AbstractPlayer(object):

    def __init__(self, **kwargs):
        super(AbstractPlayer, self).__init__()

    def play(self):
        pass

    def play_block(self):
        pass

    def stop(self):
        pass

    def is_playing(self):
        return False
    

class SoxPlayer(AbstractPlayer):
    SLUG = 'SoxPlayer'

    def __init__(self, **kwargs):
        super(SoxPlayer, self).__init__(**kwargs)
        self.playing = False
        self.proc = None
        self.delete = False
        self.onCompleteds = []
        self.hasskey = None
        self.media_player = None
        self.hassurl = None
        if config.get('/tts_engine') == 'hass-tts':
            self.hassurl = config.get('/hass_yuyin/url')
            self.hasskey = config.get('/hass_yuyin/key')
            self.media_player = config.get('/hass_yuyin/media_player')

    def hassplay(self, path):
        headers = {'Authorization': self.hasskey, 'content-type': 'application/json'}
        p = json.dumps({"entity_id": self.media_player, "media_content_id": path, "media_content_type": "music"})
        s = "/api/services/media_player/play_media"
        url_s = self.hassurl + s
        request = requests.post(url_s, headers=headers, data=p)
        if format(request.status_code) == "200" or format(request.status_code) == "201":
            return True
        else:
            logger.error(format(request.status_code))
            return False

    def ishassplayshell(self):
        cmd = 'curl -k --silent "{}/api/states/{}" -H "Authorization: {}" -H "Content-Type: application/json"'.format(self.hassurl, self.media_player, self.hasskey)
        reqjson = json.loads(os.popen(cmd).read())
        state = reqjson["state"]
        if state == "ldle" or state == "paused":
            return False
        else:
            return True

    def ishassplaying(self):
        headers = {'Authorization': self.hasskey, 'content-type': 'application/json'}
        s = "/api/states/" + self.media_player
        url_s = self.hassurl + s
        reqjson = requests.get(url=url_s, headers=headers).json()
        state = reqjson["state"]
        if state == "ldle" or state == "paused":
            return False
        else:
            return True

    def doHassPlay(self):
        self.playing = True
        if self.src.startswith('http'):
            if self.hassplay(self.src) == False:
                logger.critical('hass播放失败！ {}'.format(self.src))
            time.sleep(3)
        else:
            while(self.playing):
                self.playing = self.ishassplayshell()
                time.sleep(0.1)
            logger.info('现在hass播放状态: {}'.format(self.playing))
            for onCompleted in self.onCompleteds:
                if onCompleted is not None:
                    onCompleted()
                    
    def doPlay(self):
        cmd = ['play', str(self.src)]
        logger.debug('Executing %s', ' '.join(cmd))
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.playing = True
        self.proc.wait()
        self.playing = False
        if self.delete:
            utils.check_and_delete(self.src)
        logger.debug('play completed')
        if self.proc.returncode == 0:
            for onCompleted in self.onCompleteds:
                if onCompleted is not None:
                    onCompleted()
                              
    def play(self, src, delete=False, onCompleted=None, wait=False):
        if os.path.exists(src) or self.hassurl is None:
            self.src = src
            self.delete = delete
            if onCompleted is not None:
                self.onCompleteds.append(onCompleted)
            if not wait:
                thread.start_new_thread(self.doPlay, ())
            else:
                self.doPlay()
        elif self.hassurl is not None:
            self.src = src
            self.delete = delete
            if onCompleted is not None:
                self.onCompleteds.append(onCompleted)
            #if not wait:  #mark一下，有坑
            #    thread.start_new_thread(self.doHassPlay, ())
            #else:
            self.doHassPlay()
        else:
            logger.critical('path not exists: {}'.format(src))

    def appendOnCompleted(self, onCompleted):
        if onCompleted is not None:
            self.onCompleteds.append(onCompleted)

    def play_block(self):
        self.run()

    def stop(self):
        if self.proc:
            self.onCompleteds = []
            self.proc.terminate()
            if self.delete:
                utils.check_and_delete(self.src)

    def is_playing(self):
        return self.playing


class MusicPlayer(SoxPlayer):
    """
    给音乐播放器插件使用的，
    在 SOXPlayer 的基础上增加了列表的支持，
    并支持暂停和恢复播放
    """
    SLUG = 'MusicPlayer'

    def __init__(self, playlist, plugin, **kwargs):
        super(MusicPlayer, self).__init__(**kwargs)
        self.playlist = playlist
        self.plugin = plugin
        self.idx = 0
        self.pausing = False
        self.last_paused = None

    def update_playlist(self, playlist):
        super().stop()
        self.playlist = playlist
        self.idx = 0
        self.play()

    def play(self):
        logger.debug('MusicPlayer play')
        path = self.playlist[self.idx]
        super().stop()
        super().play(path, False, self.next)

    def next(self):
        logger.debug('MusicPlayer next')
        super().stop()
        self.idx = (self.idx+1) % len(self.playlist)
        self.play()

    def prev(self):
        logger.debug('MusicPlayer prev')
        super().stop()
        self.idx = (self.idx-1) % len(self.playlist)
        self.play()

    def pause(self):
        logger.debug('MusicPlayer pause')
        self.pausing = True

    def stop(self):
        if self.proc:
            logger.debug('MusicPlayer stop')
            # STOP current play process
            self.last_paused = utils.write_temp_file(str(self.proc.pid), 'pid', 'w')
            self.onCompleteds = []
            subprocess.run(['pkill', '-STOP', '-F', self.last_paused])

    def resume(self):
        logger.debug('MusicPlayer resume')
        self.pausing = False
        self.onCompleteds = [self.next]
        if self.last_paused is not None:
            print(self.last_paused)
            subprocess.run(['pkill', '-CONT', '-F', self.last_paused])

    def is_playing(self):
        return self.playing

    def is_pausing(self):
        return self.pausing

    def turnUp(self):
        system = platform.system()
        if system == 'Darwin':
            res = subprocess.run(['osascript', '-e', 'output volume of (get volume settings)'], shell=False, capture_output=True, universal_newlines=True)
            volume = int(res.stdout.strip())
            volume += 20
            if volume >= 100:
                volume = 100
                self.plugin.say('音量已经最大啦', wait=True)
            subprocess.run(['osascript', '-e', 'set volume output volume {}'.format(volume)])
        elif system == 'Linux':
            res = subprocess.run(["amixer sget Master | grep 'Mono:' | awk -F'[][]' '{ print $2 }'"], shell=True, capture_output=True, universal_newlines=True)
            print(res.stdout)
            if res.stdout != '' and res.stdout.strip().endswith('%'):
                volume = int(res.stdout.strip().replace('%', ''))
                volume += 20
                if volume >= 100:
                    volume = 100
                    self.plugin.say('音量已经最大啦', wait=True)
                subprocess.run(['amixer', 'set', 'Master', '{}%'.format(volume)])
            else:
                subprocess.run(['amixer', 'set', 'Master', '20%+'])
        else:
            self.plugin.say('当前系统不支持调节音量', wait=True)
        self.resume()

    def turnDown(self):
        system = platform.system()
        if system == 'Darwin':
            res = subprocess.run(['osascript', '-e', 'output volume of (get volume settings)'], shell=False, capture_output=True, universal_newlines=True)
            volume = int(res.stdout.strip())
            volume -= 20
            if volume <= 20:
                volume = 20
                self.plugin.say('音量已经很小啦', wait=True)
            subprocess.run(['osascript', '-e', 'set volume output volume {}'.format(volume)])
        elif system == 'Linux':
            res = subprocess.run(["amixer sget Master | grep 'Mono:' | awk -F'[][]' '{ print $2 }'"], shell=True, capture_output=True, universal_newlines=True)
            if res.stdout != '' and res.stdout.endswith('%'):
                volume = int(res.stdout.replace('%', '').strip())
                volume -= 20
                if volume <= 20:
                    volume = 20
                    self.plugin.say('音量已经最小啦', wait=True)
                subprocess.run(['amixer', 'set', 'Master', '{}%'.format(volume)])
            else:
                subprocess.run(['amixer', 'set', 'Master', '20%-'])
        else:
            self.plugin.say('当前系统不支持调节音量', wait=True)
        self.resume()
