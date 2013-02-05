#!/usr/bin/env python
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

# --------------------------------------------------------------------
# Constants

ALL_URL = 'https://www.coursera.org/maestro/api/topic/list?full=1'
LOGIN_PATH = '/auth/auth_redirector?type=login&subtype=normal&email=&visiting=&minimal=true'
LECTURES_PATH = "/lecture/index"

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
        print_course_list()
        return

    # Find the given course
    matches = find_course(course_name)
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

def get_opener():
    """
    Return a urllib2 opener that saves cookies
    """
    if not hasattr(get_opener, 'opener'):
        cj = cookielib.CookieJar()
        opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cj))
        setattr(get_opener, 'cookiejar', cj)
        setattr(get_opener, 'opener', opener)
    return getattr(get_opener, 'opener')

def readurl(url, data=None, is_head=False):
    """
    Read a given URL.
    """
    debug("Reading %s with data %s" % (url, data))

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
    res = get_opener().open(req)
    debug(getattr(get_opener, 'cookiejar'))
    return res

def bsoup(url):
    """
    Parse a given URL with Beautiful Soup.  Seems to sometimes have
    trouble with lxml, so force it to use html.parser.
    """
    return BeautifulSoup(readurl(url), 'html.parser')

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

def all_courses():
    """
    Return the JSON from reading the list of all courses from
    Coursera's website.
    """
    return json.load(readurl(ALL_URL))

def print_course_list():
    """
    Download the list of all courses, and print each course's short
    name
    """
    courses = all_courses()
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

def find_course(short_name):
    """
    Returns a list of courses matching the given course short_name.
    We expect no more than one match, but return a list to be safe.
    """
    return [course for course in all_courses()
            if course['short_name'] == short_name]

def get_lecture_info(lectures_url):
    """
    Given a Coursera url which inludes the listing of all the
    lectures, parse the page and just a list of the relevant info
    about each lecture.
    """
    page = bsoup(lectures_url)

    # Go through all the links.  The lecture links are tagged with the
    # class 'lecture-link'.  They look like this:
    # 
    # <a class="lecture-link" data-lecture-id="124" data-modal=".course-modal-frame" data-modal-iframe="https://class.coursera.org/nlp/lecture/preview_view?lecture_id=124" href="https://class.coursera.org/nlp/lecture/preview_view/124" rel="lecture-link">
    # Course Introduction (14:11)</a>
    #
    lectures = []
    name_re = '^(.*)\((\d+:\d+)\)$'
    for link in page.find_all('a', attrs={'class': 'lecture-link'}):
        vidlink = link['data-modal-iframe']
        # strip because video names tend to start with a \n
        vidtext = link.text.strip()
        match = re.match(name_re, vidtext)
        if match is not None:
            (name, duration) = match.groups()
        else:
            name = vidtext
            duration = None
        vidpage = bsoup(vidlink)
        mp4url = vidpage.find('source', attrs={'type': 'video/mp4'})['src']
        vidinfo = readurl(mp4url, is_head=True)
        size = vidinfo.headers['Content-Length']
        lectures.append([name, duration, size, mp4url])

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
    newurl = readurl(course_url + LOGIN_PATH).geturl()
    return readurl(newurl, {'email':username,
                            'password':password,
                            'login': 'Login'})

def get_current_instance(course_info):
    """
    Return the information about the currently running instance of the
    course.
    """
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
<atom:link rel="self" href="http://www.matehat.com/coursera-podcast/feeds/algorithms-design-and-analysis-part-2.xml" type="application/rss+xml"/>
'''.format(course_info['name'],
           instance_info['home_link'],
           course_info['instructor'],
           course_info['short_description'],
           course_info['large_icon']
           )

def rss_footer():
    return '''
</channel>
</rss>
'''

def rss_lecture_info(course_info, lecture_data):
    rss_lectures = []
    # This bogus date is just so that the lectures appear in order
    pub_date = datetime.strptime('%s0101' % date.today().year, '%Y%m%d')
    oneday = timedelta(days=1)
    for lecture in lecture_data:
        (name, duration, size, mp4url) = lecture
        rss_lectures.append('''
<item>
<title>{0}</title>
<itunes:author>{1}</itunes:author>
<enclosure url="{2}" length="{3}" type="video/mp4"/>
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
           pub_date.strftime("%a, %d %b %Y 12:00:00 -0500"),
           duration,
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

