import os, random, re, shutil, socket, sys, urllib
from Cheetah.Template import Template
from Cheetah.Filters import Filter
from plugin import Plugin
from xml.sax.saxutils import escape
from lrucache import LRUCache
from urlparse import urlparse
import eyeD3

SCRIPTDIR = os.path.dirname(__file__)

CLASS_NAME = 'Music'

PLAYLISTS = ('.m3u', '.ram', '.pls', '.b4s', '.wpl', '.asx', '.wax', '.wvx')

# Search strings for different playlist types
asxfile = re.compile('ref +href *= *"(.+)"', re.IGNORECASE).search
wplfile = re.compile('media +src *= *"(.+)"', re.IGNORECASE).search
b4sfile = re.compile('Playstring="file:(.+)"').search
plsfile = re.compile('[Ff]ile(\d+)=(.+)').match
plstitle = re.compile('[Tt]itle(\d+)=(.+)').match
plslength = re.compile('[Ll]ength(\d+)=(\d+)').match

if os.path.sep == '/':
    quote = urllib.quote
    unquote = urllib.unquote_plus
else:
    quote = lambda x: urllib.quote(x.replace(os.path.sep, '/'))
    unquote = lambda x: urllib.unquote_plus(x).replace('/', os.path.sep)

class FileData:
    def __init__(self, name, isdir):
        self.name = name
        self.isdir = isdir
        self.isplay = os.path.splitext(name)[1].lower() in PLAYLISTS
        self.title = ''
        self.duration = 0

class EncodeUnicode(Filter):
    def filter(self, val, **kw):
        """Encode Unicode strings, by default in UTF-8"""

        if kw.has_key('encoding'):
            encoding = kw['encoding']
        else:
            encoding='utf8'
                            
        if type(val) == type(u''):
            filtered = val.encode(encoding)
        else:
            filtered = str(val)
        return filtered

class Music(Plugin):

    CONTENT_TYPE = 'x-container/tivo-music'

    AUDIO = 'audio'
    DIRECTORY = 'dir'
    PLAYLIST = 'play'

    media_data_cache = LRUCache(300)

    def send_file(self, handler, container, name):
        o = urlparse("http://fake.host" + handler.path)
        path = unquote(o[2])
        fname = container['path'] + path[len(name) + 1:]
        fsize = os.path.getsize(fname)
        handler.send_response(200)
        handler.send_header('Content-Type', 'audio/mpeg')
        handler.send_header('Content-Length', fsize)
        handler.send_header('Connection', 'close')
        handler.end_headers()
        f = file(fname, 'rb')
        shutil.copyfileobj(f, handler.wfile)

    def QueryContainer(self, handler, query):

        def AudioFileFilter(f, filter_type=None):

            if filter_type:
                filter_start = filter_type.split('/')[0]
            else:
                filter_start = filter_type

            if os.path.isdir(f):
                ftype = self.DIRECTORY

            elif eyeD3.isMp3File(f):
                ftype = self.AUDIO
            elif os.path.splitext(f)[1].lower() in PLAYLISTS:
                ftype = self.PLAYLIST
            else:
                ftype = False

            if filter_start == self.AUDIO:
                if ftype == self.AUDIO:
                    return ftype
                else:
                    return False
            else: 
                return ftype

        def media_data(f):
            if f.name in self.media_data_cache:
                return self.media_data_cache[f.name]

            item = {}
            item['path'] = f.name
            item['part_path'] = f.name.replace(local_base_path, '')
            item['name'] = os.path.split(f.name)[1]
            item['is_dir'] = f.isdir
            item['is_playlist'] = f.isplay

            if f.title:
                item['Title'] = f.title

            if f.duration > 0:
                item['Duration'] = f.duration

            if f.isdir or f.isplay or '://' in f.name:
                self.media_data_cache[f.name] = item
                return item

            try:
                audioFile = eyeD3.Mp3AudioFile(f.name)
                item['Duration'] = audioFile.getPlayTime() * 1000

                tag = audioFile.getTag()
                artist = tag.getArtist()
                title = tag.getTitle()
                if artist == 'Various Artists' and '/' in title:
                    artist, title = title.split('/')
                item['ArtistName'] = artist.strip()
                item['SongTitle'] = title.strip()
                item['AlbumTitle'] = tag.getAlbum()
                item['AlbumYear'] = tag.getYear()
                item['MusicGenre'] = tag.getGenre().getName()
            except Exception, msg:
                print msg

            self.media_data_cache[f.name] = item
            return item

        subcname = query['Container'][0]
        cname = subcname.split('/')[0]
        local_base_path = self.get_local_base_path(handler, query)

        if not handler.server.containers.has_key(cname) or \
           not self.get_local_path(handler, query):
            handler.send_response(404)
            handler.end_headers()
            return

        if os.path.splitext(subcname)[1].lower() in PLAYLISTS:
            t = Template(file=os.path.join(SCRIPTDIR, 'templates', 'm3u.tmpl'),
                         filter=EncodeUnicode)
            t.files, t.total, t.start = self.get_playlist(handler, query)
        else:
            t = Template(file=os.path.join(SCRIPTDIR,'templates', 
                         'container.tmpl'), filter=EncodeUnicode)
            t.files, t.total, t.start = self.get_files(handler, query,
                                                       AudioFileFilter)
        t.files = map(media_data, t.files)
        t.container = cname
        t.name = subcname
        t.quote = quote
        t.escape = escape
        page = str(t)

        handler.send_response(200)
        handler.send_header('Content-Type', 'text/xml')
        handler.send_header('Content-Length', len(page))
        handler.send_header('Connection', 'close')
        handler.end_headers()
        handler.wfile.write(page)

    def parse_playlist(self, list_name, recurse):
        try:
            url = list_name.index('http://')
            list_name = list_name[url:]
            list_file = urllib.urlopen(list_name)
        except:
            list_file = open(list_name)
            local_path = os.path.sep.join(list_name.split(os.path.sep)[:-1])

        ext = os.path.splitext(list_name)[1].lower()

        if ext in ('.wpl', '.asx', '.wax', '.wvx', '.b4s'):
            playlist = []
            for line in list_file:
                if ext == '.wpl':
                    s = wplfile(line)
                elif ext == '.b4s':
                    s = b4sfile(line)
                else:
                    s = asxfile(line)
                if s:
                    playlist.append(FileData(s.group(1), False))

        elif ext == '.pls':
            names, titles, lengths = {}, {}, {}
            for line in list_file:
                s = plsfile(line)
                if s:
                    names[s.group(1)] = s.group(2)
                else:
                    s = plstitle(line)
                    if s:
                        titles[s.group(1)] = s.group(2)
                    else:
                        s = plslength(line)
                        if s:
                            lengths[s.group(1)] = int(s.group(2))
            playlist = []
            for key in names:
                f = FileData(names[key], False)
                if key in titles:
                    f.title = titles[key]
                if key in lengths:
                    f.duration = lengths[key]
                playlist.append(f)

        else: # ext == '.m3u' or '.ram'
            duration, title = 0, ''
            playlist = []
            for x in list_file:
                x = x.strip()
                if x:
                    if x.startswith('#EXTINF:'):
                        try:
                            duration, title = x[8:].split(',')
                            duration = int(duration)
                        except ValueError:
                            duration = 0

                    elif not x.startswith('#'):
                        f = FileData(x, False)
                        f.title = title.strip()
                        f.duration = duration
                        playlist.append(f)
                        duration, title = 0, ''

        list_file.close()

        # Expand relative paths
        for i in xrange(len(playlist)):
            if not '://' in playlist[i].name:
                name = playlist[i].name
                if not os.path.isabs(name):
                    name = os.path.join(local_path, name)
                playlist[i].name = os.path.normpath(name)

        if recurse:
            newlist = []
            for i in playlist:
                if i.isplay:
                    newlist.extend(self.parse_playlist(i.name, recurse))
                else:
                    newlist.append(i)

            playlist = newlist

        return playlist

    def get_files(self, handler, query, filterFunction=None):

        def build_recursive_list(path, recurse=True):
            files = []
            for f in os.listdir(path):
                f = os.path.join(path, f)
                isdir = os.path.isdir(f)
                if recurse and isdir:
                    files.extend(build_recursive_list(f))
                else:
                   fd = FileData(f, isdir)
                   if recurse and fd.isplay:
                       files.extend(self.parse_playlist(f, recurse))
                   elif isdir or filterFunction(f, file_type):
                       files.append(fd)
            return files

        def dir_sort(x, y):
            if x.isdir == y.isdir:
                if x.isplay == y.isplay:
                    return name_sort(x, y)
                else:
                    return y.isplay - x.isplay
            else:
                return y.isdir - x.isdir

        def name_sort(x, y):
            return cmp(x.name, y.name)

        subcname = query['Container'][0]
        cname = subcname.split('/')[0]
        path = self.get_local_path(handler, query)

        file_type = query.get('Filter', [''])[0]

        recurse = query.get('Recurse',['No'])[0] == 'Yes'
        filelist = build_recursive_list(path, recurse)

        # Sort
        if query.get('SortOrder',['Normal'])[0] == 'Random':
            seed = query.get('RandomSeed', ['1'])[0]
            self.random_lock.acquire()
            random.seed(seed)
            random.shuffle(filelist)
            self.random_lock.release()
        else:
            filelist.sort(dir_sort)

        # Trim the list
        return self.item_count(handler, query, cname, filelist)

    def get_playlist(self, handler, query):
        subcname = query['Container'][0]
        cname = subcname.split('/')[0]

        try:
            url = subcname.index('http://')
            list_name = subcname[url:]
        except:
            list_name = self.get_local_path(handler, query)

        recurse = query.get('Recurse',['No'])[0] == 'Yes'
        playlist = self.parse_playlist(list_name, recurse)

        # Trim the list
        return self.item_count(handler, query, cname, playlist)
