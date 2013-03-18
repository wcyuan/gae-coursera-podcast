#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
This script creates podcasts of Coursera courses.  It reads from parts
of the Coursera website and outputs xml RSS files.

It tries to use the previews of lectures that some Coursera courses
seem to have, which allows us to read the list of lectures without
logging in or having a user that has signed up for the course.
Otherwise, it needs a username and password.

This script owes some of its logic to
  https://github.com/jplehmann/coursera
and
  https://github.com/matehat/coursera-podcast

There are three ways to run the script.

 cousera.py

If run with no arguments, it will output a list of all the course's
short names.

 cousera.py [course-short-name]

If run with the short name of a course, it will output a list of that
course's lectures.

 cousera.py --xml [course-short-name]

If run with the --xml option and a course name, it will output an RSS
XML for a podcast that includes all the lectures (so far) for that
course.

Really, this should use the requests python library to parse web
pages.  http://docs.python-requests.org/en/latest/.  However, that
library is not yet compatible with Google App Engine according to
http://stackoverflow.com/questions/9762685/using-the-requests-python-library-in-google-app-engine
and
https://github.com/kennethreitz/requests/pull/493#issuecomment-4556331.

Another possibility is to use Mechanize.  Mechanize doesn't
immediately work with Google App Engine, but there are patches that
work, such as https://code.google.com/p/gaemechanize2/ (also see
http://stackoverflow.com/questions/1902079/python-mechanize-gaepython-code/2056543#2056543)

At this point, I'm just following
https://github.com/jplehmann/coursera.  There was a recent change to
Coursera Authetication which they deal with in
https://github.com/jplehmann/coursera/commit/6e2725edf9e5c5be88ed5a95c84f6834ada012a0

"""

# We would ideally use requests and mechanize, but I want this to be
# usable on Google App Engine, so I'm sticking to the older tech.
from   bs4        import BeautifulSoup
import cookielib
from   datetime   import datetime, timedelta, date
from   itertools  import izip_longest
import json
from   logging    import getLogger, DEBUG, debug
from   optparse   import OptionParser
import re
import urllib
import urllib2
import tempfile
import os
import StringIO

# --------------------------------------------------------------------
# Constants

ALL_URL = 'https://www.coursera.org/maestro/api/topic/list?full=1'
LOGIN_PATH = '/auth/auth_redirector?type=login&subtype=normal&email=&visiting=&minimal=true'
LECTURES_PATH = "/lecture/index"
TIME_FORMAT = "%a, %d %b %Y %H:%M:%S -0500"
AUTH_URL = 'https://www.coursera.org/maestro/api/user/login'

# Couldn't figure out how to login to the main Coursera page and
# download the list of courses that you are subscribed to.  I think it
# comes from this page, but only after having autheticated.
#
# https://www.coursera.org/maestro/api/topic/list_my?user_id=101589

# --------------------------------------------------------------------
# Main and command line arguments

def main():
    opts, course_name = getopts()

    # If we weren't given a course, just print all the courses.
    if course_name is None:
        print_course_list(courses_file=opts.courses)
        return

    # Find the given course
    matches = find_course(course_name, courses_file=opts.courses)
    if len(matches) < 1:
        raise ValueError("Can't find course")
    if len(matches) > 1:
        raise ValueError("Too many matches for course")
    course_info = matches[0]

    # Get information about each of the lectures.  This might return
    # None, if thre is no preview available for the course.
    lecture_data = get_preview_lectures(course_info)
    if lecture_data is None:
        debug("No preview for course %s" % course_info['short_name'])
        if opts.username is None or opts.password is None:
            print "Can't continue without username and password"
        lecture_data = get_current_lectures(course_info,
                                            opts.username,
                                            opts.password)

    # Print the course and its lectures in the desired format.
    if opts.xml:
        print course_rss(course_info, lecture_data)
    else:
        print texttable(lecture_data)

def getopts():
    """
    parse command line
    """
    parser = OptionParser()
    parser.add_option('--verbose',
                      action='store_true',
                      help='Verbose mode')
    parser.add_option('--xml',
                      action='store_true',
                      help='Output XML RSS format')
    parser.add_option('-u', '--username',
                      help='Cousera username')
    parser.add_option('-p', '--password',
                      help='Output XML RSS format')
    parser.add_option('--courses',
                      help='file with full list of courses')
    opts, args = parser.parse_args()
    if opts.verbose:
        getLogger().setLevel(DEBUG)

    if len(args) == 0:
        course_name = None
    elif len(args) == 1:
        course_name = args[0]
    else:
        raise ValueError("Too many arguments: %s" % ', '.join(args))

    return opts, course_name

# --------------------------------------------------------------------
# Reading and parsing web pages

class ReadUrl(object):
    def __init__(self):
        # Make a urllib2 opener that saves cookies
        self.csrftoken = None
        self.session   = None
        self.cj        = cookielib.CookieJar()
        self.opener    = None

    def set_headers(self, opener):
        if self.csrftoken is not None and self.session is not None:
            opener.addheaders.append(('Cookie', 'csrf_token=%s;session=%s' %
                                           (self.csrftoken, self.session)))
        elif self.csrftoken is not None:
            opener.addheaders.append(('Cookie', 'csrftoken=%s' % self.csrftoken))
            opener.addheaders.append(('Referer', 'https://www.coursera.org'))
            opener.addheaders.append(('X-CSRFToken', self.csrftoken))

    def readurl(self, url, data=None, is_head=False, headers=False):
        """
        Read a given URL.
        """
        debug("Reading %s with data %s" % (url, data))
        debug(self.cj)
        opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(self.cj))
        if headers:
            self.set_headers(opener)
        debug(opener.addheaders)

        # Encode any params
        if data is not None:
            data = urllib.urlencode(data)

        # Create the request and open it.  In order to use cookies, we
        # always use the standard opener to read pages.  An alternative
        # would have been to set the default opener with
        #
        #   urllib2.install_opener(get_opener())
        #
        # and then use urllib2.urlopen
        req = urllib2.Request(url, data)
        if is_head:
            req.get_method = lambda : 'HEAD'
        res = opener.open(req)
        debug(res.headers.items())
        self.save_cookies()
        opener.close()
        return res

    def save_cookies(self):
        for cookie in self.cj:
            if cookie.name == 'csrf_token':
                if self.csrftoken is not None:
                    debug("Ignoring second CSRF {0}".format(cookie.value))
                    continue
                self.csrftoken = cookie.value
                debug("Got CSRF {0}".format(self.csrftoken))
            elif cookie.name == 'session':
                if self.session is not None:
                    debug("Ignoring second session {0}".format(cookie.value))
                    continue
                self.session = cookie.value
                debug("Got session {0}".format(self.csrftoken))
            else:
                debug("Skipping cookie {0}".format(cookie))

    def bsoup(self, url, headers=False):
        """
        Parse a given URL with Beautiful Soup.  Seems to sometimes have
        trouble with lxml, so force it to use html.parser.
        """
        return BeautifulSoup(self.readurl(url, headers=headers), 'html.parser')

READURL=ReadUrl()

# --------------------------------------------------------------------
# Formatting text

def texttable(table, delim=' '):
    """
    Print a table (represented as a list of lists) such that the
    columns line up (every column has the width to accomodate the
    widest row)
    """
    widths = (max(len(fld) for fld in line)
              for line in izip_longest(*table, fillvalue=""))
    formats = ["%-{0}s".format(width) for width in widths]
    return "\n".join(delim.join(format % fld
                                for (format, fld) in zip(formats, line))
                    for line in table)

# --------------------------------------------------------------------
# Functions for course list
#

def all_courses(courses_file=None):
    """
    Return the JSON from reading the list of all courses from
    Coursera's website.
    """
    if courses_file is None:
        data = READURL.readurl(ALL_URL)
    else:
        data = open(courses_file)
    return json.load(data)

def print_course_list(courses_file=None):
    """
    Download the list of all courses, and print each course's short
    name
    """
    courses = all_courses(courses_file=None)
    lines = []
    for ii in range(len(courses)):
        course_info = courses[ii]
        # Each course could be offered many times, like every year or
        # every few months.  So each course has many instances, and
        # each instance could have its own course webpage and
        # materials.
        for instance in course_info['courses']:
            lines.append([str(ii),
                          str(course_info['short_name']),
                          '%s/%s' % (instance['start_month'],
                                     instance['start_year']),
                          "ACTIVE" if instance['active'] else 'INACTIVE', 
                          str(instance['home_link']),
                          str(course_info['preview_link'])])
        if len(course_info['courses']) == 0:
            lines.append([str(ii),
                          str(course_info['short_name']),
                          'None',
                          "INACTIVE",
                          "No-instance",
                          str(course_info['preview_link'])])
    print texttable(lines)

# --------------------------------------------------------------------
# Functions for a specific course, previews
#

def find_course(short_name, courses_file=None):
    """
    Returns a list of courses matching the given course short_name.
    We expect no more than one match, but return a list to be safe.
    """
    return [course for course in all_courses(courses_file)
            if course['short_name'] == short_name]

def get_lecture_info(lectures_url):
    """
    Given a Coursera url which inludes the listing of all the
    lectures, parse the page and just a list of the relevant info
    about each lecture.
    """
    page = READURL.bsoup(lectures_url, headers=True)

    # Go through all the links.  The lecture links are tagged with the
    # class 'lecture-link'.  They look like this:
    # 
    # <a class="lecture-link" data-lecture-id="124" data-modal=".course-modal-frame" data-modal-iframe="https://class.coursera.org/nlp/lecture/preview_view?lecture_id=124" href="https://class.coursera.org/nlp/lecture/preview_view/124" rel="lecture-link">
    # Course Introduction (14:11)</a>
    #
    lectures = []
    name_re = '^(.*)[\(\[](\d+:\d+)[\)\]]$'

    weeks = page.find_all('div', attrs={'class': 'course-item-list-header'})
    for week in weeks:
        week_desc = week.text.strip()
        lecture_list = week.next_sibling
        for link in lecture_list.find_all('a', attrs={'class': 'lecture-link'}):
            vidlink = link['data-modal-iframe']
            # strip because video names tend to start with a \n
            vidtext = link.text.strip()
            match = re.match(name_re, vidtext)
            if match is not None:
                (name, duration) = match.groups()
            else:
                name = vidtext
                duration = ''
            vidpage = READURL.bsoup(vidlink)
            mp4url = vidpage.find('source', attrs={'type': 'video/mp4'})['src']
            vidinfo = READURL.readurl(mp4url, is_head=True)
            size = vidinfo.headers['Content-Length']
            description = "%s : %s" % (week_desc, name)
            full_name = "%s - %s" % (week_desc[:13], name)
            lectures.append([full_name, duration, size, mp4url, description])

    return lectures

def get_preview_lectures(course_info):
    """
    Given the JSON information about a course, get the lectures from
    the course's preview page.
    """
    if course_info['preview_link'] is None:
        return None
    if course_info['preview_link'] == "":
        return None
    return get_lecture_info(course_info['preview_link'])

# --------------------------------------------------------------------
# Functions for a specific course, login required
#

def login(course_url, username, password):
    """
    Login to a Coursera course with the given username and password
    """
    # first read the LECTURE_PATH to set the CSRFToken
    READURL.readurl(course_url + LECTURES_PATH)

    # then read the AUTH_URL with username and password set
    READURL.readurl(AUTH_URL, {'email_address':username,
                               'password':password}, headers=True)

    # then read the LOGIN_PATH (auth-redirector) to get the session id
    READURL.readurl(course_url + LOGIN_PATH)
    return

def get_current_instance(course_info):
    """
    Return the information about the currently running instance of the
    course.
    """
    # Return None if there are no instances of the course at all
    if len(course_info['courses']) == 0:
        return None
    # The last active instance of the course is generally the
    # currently running one.
    instances = [instance for instance in course_info['courses']
                 if instance['active']]
    if len(instances) == 0:
        # If there are no active courses, just return the last one.
        return course_info['courses'][-1]
    else:
        return instances[-1]

def get_current_lectures(course_info, username, password):
    """
    Get the current set of lectures for a given course.

    Note, it seems that once you get the lecture urls, you can
    download the videos without logging in.
    """
    current = get_current_instance(course_info)
    # home_link looks like:
    #   http://class.coursera.org/<short_name><suffix>    
    # where the suffix indicates which instance of the course this is
    home = current['home_link']
    login(home, username, password)
    return get_lecture_info(home + LECTURES_PATH)

# --------------------------------------------------------------------
# Functions for outputting XML RSS information
#

def rss_header():
    return '''
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">
<channel>
'''

def rss_course_info(course_info):
    instance_info = get_current_instance(course_info)
    return '''
<title>{0}</title>
<link>{1}</link>
<language>en-us</language>
<copyright>Copyright 2012 Coursera</copyright>
<itunes:author>{2}</itunes:author>
<itunes:summary>
{3}
</itunes:summary>
<description>
{3}
</description>
<itunes:image href="{4}"/>
<atom:link rel="self" href="http://gae-coursera-podcast.appspot.com/course?name={5}" type="application/rss+xml"/>
'''.format(course_info['name'].encode('ascii', 'ignore'),
           instance_info['home_link'],
           course_info['instructor'].encode('ascii', 'ignore'),
           course_info['short_description'].encode('ascii', 'ignore'),
           course_info['large_icon'],
           course_info['short_name'],
           )

def rss_footer():
    return '''
</channel>
</rss>
'''

def rss_lecture_info(course_info, lecture_data):
    rss_lectures = []
    # This bogus date is just so that the lectures appear in order
    pub_date = datetime.strptime(datetime.now().strftime("%Y0101 %H:%M:%S"),
                                 '%Y%m%d %H:%M:%S')
    oneday = timedelta(days=1)
    for lecture in lecture_data:
        (name, duration, size, mp4url, description) = lecture
        rss_lectures.append('''
<item>
<title>{0}</title>
<itunes:author>{1}</itunes:author>
<enclosure url="{2}" length="{3}" type="video/mp4"/>
<description>
{6}
</description>
<guid>
{2}
</guid>
<pubDate>{4}</pubDate>
<itunes:duration>{5}</itunes:duration>
</item>
'''.format(name,
           course_info['instructor'],
           mp4url,
           size,
           pub_date.strftime(TIME_FORMAT),
           duration,
           description,
           ))
        pub_date += oneday
    return ''.join(rss_lectures)

def course_rss(course_info, lecture_data):
    """
    Returns an XML file (as a string) which represents an RSS feed for
    this course, with an item for each lecture.
    """
    return ''.join((rss_header(),
                    rss_course_info(course_info),
                    rss_lecture_info(course_info, lecture_data),
                    rss_footer()))

# --------------------------------------------------------------------

if __name__ == "__main__":
    main()

