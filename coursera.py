import json
import urllib2

csj = json.load(urllib2.urlopen("https://www.coursera.org/maestro/api/topic/list?full=1"))
