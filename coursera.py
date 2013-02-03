#!/usr/bin/env python
"""
This script creates podcasts of Coursera courses.

It reads from parts of the Coursera website and outputs xml RSS files.

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

If run with the --xml option and a course name, 

"""

# We would ideally use requests and mechanize, but I want this to be
# usable on Google App Engine, so I'm sticking to the older tech.
from   bs4        import BeautifulSoup
import cookielib
from   itertools  import izip_longest
import json
from   logging    import getLogger, DEBUG, debug
from   optparse   import OptionParser
import urllib
import urllib2

#################################################

ALL_URL = 'https://www.coursera.org/maestro/api/topic/list?full=1'
LOGIN_URL = 'https://class.coursera.org/%s/auth/auth_redirector?type=login&subtype=normal&email=&visiting=&minimal=true'

#LOGIN_URL1='https://www.coursera.org/maestro/api/user/login'
#https://www.coursera.org/maestro/api/topic/list_my?user_id=101589
#https://eventing.coursera.org/info?key=%22pageview%22&from=%22https%3A%2F%2Fclass.coursera.org%2Fproglang-2012-001%2Fauth%2Fwelcome%3Ftype%3Dlogout%26visiting%3Dhttps%253A%252F%252Fclass.coursera.org%252Fproglang-2012-001%252Fclass%252Findex%22&session=%226563100903-1359865250923%22&client=%22home%22&url=%22https%3A%2F%2Fwww.coursera.org%2Faccount%2Fsignin%3Fr%3Dhttps%253A%252F%252Fclass.coursera.org%252Fproglang-2012-001%252Fauth%252Fauth_redirector%253Ftype%253Dlogin%2526subtype%253Dnormal%2526email%253D%2526visiting%253Dhttps%25253A%25252F%25252Fclass.coursera.org%25252Fproglang-2012-001%25252Fclass%25252Findex%22&time=135986525092
#from="https://class.coursera.org/proglang-2012-001/auth/welcome?type=logout&visiting=https%3A%2F%2Fclass.coursera.org%2Fproglang-2012-001%2Fclass%2Findex"
#https://class.coursera.org/proglang-2012-001/auth/welcome?type=logout
#visiting=https://class.coursera.org/proglang-2012-001/class/index
#url="https://www.coursera.org/account/signin?r=https%3A%2F%2Fclass.coursera.org%2Fproglang-2012-001%2Fauth%2Fauth_redirector%3Ftype%3Dlogin%26subtype%3Dnormal%26email%3D%26visiting%3Dhttps%253A%252F%252Fclass.coursera.org%252Fproglang-2012-001%252Fclass%252Findex"
#https://www.coursera.org/account/signin?r=https://class.coursera.org/proglang-2012-001/auth/auth_redirector?type=login&subtype=normal&email=&visiting=https%3A%2F%2Fclass.coursera.org%2Fproglang-2012-001%2Fclass%2Findex

#################################################

def main():
    opts, course = getopts()

    # If we weren't given a course, just print all the courses.
    if course is None:
        print_course_list()
        return

    # Find the given course
    matches = find_course(course)
    if len(matches) < 1:
        raise ValueError("Can't find course")
    if len(matches) > 1:
        raise ValueError("Too many matches for course")

    # Get information about each of the lectures.  This might return
    # None, if thre is no preview available for the course.
    lecture_data = get_lecture_data(course)
    if lecture_data is None:
        return

    # Print the course and its lectures in the desired format.
    if opts.xml:
        print_xml_lectures(course, lecture_data)
    else:
        print_lectures(course, lecture_data)

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
        course = None
    elif len(args) == 1:
        course = args[0]
    else:
        raise ValueError("Too many arguments: %s" % ', '.join(args))

    return opts, course

# --------------------------------------------------------------------

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

def readurl(url, data=None):
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

def login(course_name, username, password):
    """
    Login to a Coursera course with the given username and password
    """
    newurl = readurl(LOGIN_URL % course_name).geturl()
    return readurl(newurl, {'email_address':username, 'password':password})

# --------------------------------------------------------------------

def all_courses():
    """
    Return the JSON from reading the list of all courses from
    Coursera's website.
    """
    return json.load(readurl(ALL_URL))

def find_course(short_name):
    """
    Returns a list of courses matching the given course short_name.
    We expect no more than one match, but return a list to be safe.
    """
    return [course for course in all_courses()
            if course['short_name'] == short_name]

def texttable(table, delim=' '):
    widths = (max(len(fld) for fld in line)
              for line in izip_longest(*table, fillvalue=""))
    formats = ["%-{0}s".format(width) for width in widths]
    return "\n".join(delim.join(format % fld
                                for (format, fld) in zip(formats, line))
                    for line in table)

def print_course_list():
    """
    Download the list of all courses, and print each course's short
    name
    """
    courses = all_courses()
    lines = []
    for course in courses:
        for instance in course['courses']:
            lines.append([str(course['short_name']),
                          '%s/%s' % (instance['start_month'],
                                     instance['start_year']),
                          "ACTIVE" if instance['active'] else 'INACTIVE', 
                          str(instance['home_link']),
                          str(course['preview_link'])])
    print texttable(lines)

def get_lecture_data(course):
    """
    Given the JSON information about a course, from the list of all
    courses, get the list of lectures.
    """

    # Handle the case where the course does not offer a preview.
    if course['preview_link'] is None:
        print "No preview for course %s" % course['short_name']
        return None

    # If there is a preview, download the page with the list of
    # lectures
    preview = bsoup(course['preview_link'])

    # Go through all the links.  The lecture links are tagged with the
    # class 'lecture-link'.  They look like this:
    # 
    #
    for link in preview.find_all('a'):
        if (not link.has_key('class')):
            continue
        if 'lecture-link' not in link['class']:
            continue

#def course_videos(course):
#    preview = read_course(course)
#    items = preview.find_all('li')
#    for item in items:
#        if len(item.contents) == 0:
#            continue
#In [94]: preview.find_all('li')[2].contents[0]
#Out[94]:
#<a class="lecture-link" data-lecture-id="1" data-modal=".course-modal-frame" data-modal-iframe="https://class.coursera.org/ml/lecture/preview_view?lecture_id=1" href="https://class.coursera.org/ml/lecture/preview_view/1" rel="lecture-link">
#Welcome (7 min)</a>

#In [95]: preview.find_all('li')[2].contents[0]['class']
#Out[95]: [u'lecture-link']

#In [96]: preview.find_all('li')[2].contents[0]['href']
#Out[96]: u'https://class.coursera.org/ml/lecture/preview_view/1'

#In [100]: preview.find_all('li')[4].contents[0]['data-modal-iframe']
#Out[100]: u'https://class.coursera.org/ml/lecture/preview_view?lecture_id=3'

        

# --------------------------------------------------------------------

if __name__ == "__main__":
    main()

