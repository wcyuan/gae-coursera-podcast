"""
A Google App Engine App that makes podcasts of Coursera lectures
"""

# We would ideally use requests and mechanize, but I want this to be
# usable on Google App Engine, so I'm sticking to the older tech.
import coursera_rss
from   datetime import datetime, timedelta, date
import jinja2
import os
import webapp2
import logging

from google.appengine.ext import db
from google.appengine.api import users as gusers
from google.appengine.api import mail

jinja_environment = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)))

# --------------------------------------------------------------------
# Data Models

class Course(db.Model):
    """
    Models a Coursera course
    """
    full_name   = db.StringProperty()
    instructor  = db.StringProperty()
    start_date  = db.StringProperty()
    icon_url    = db.StringProperty()
    url         = db.StringProperty()
    description = db.TextProperty()
    preview_url = db.StringProperty()
    # This is the last time we created an rss file for this course
    last_updated = db.DateTimeProperty()

    @classmethod
    def make_key(cls, name):
        """
        Constructs a Datastore key for a Course.
        """
        return db.Key.from_path('Course', name)

    def preview_text(self):
        if self.preview_url is None or self.preview_url == '':
            return ""
        return "Preview"

    def pubDate(self):
        return datetime.now().strftime(coursera_rss.TIME_FORMAT)

    def lastBuildDate(self):
        return datetime.now().strftime(coursera_rss.TIME_FORMAT)

class Lecture(db.Model):
    """
    Models a lecture in a course.
    """
    name     = db.StringProperty()
    duration = db.StringProperty()
    size     = db.StringProperty()
    url      = db.StringProperty(required=True)
    description = db.StringProperty()

    @classmethod
    def make_key(cls, course_name, index):
        """
        Construct a Lecture key from a course and an index
        """
        return db.Key.from_path('Course', course_name, 'Lecture', index)

    def pubDate(self):
        """
        The pub date is just a bogus date to make the lecture appear
        in the same order as on the screen.

        Note that some courses list the newest week first and some
        list the newest week last, and we'll do whatever order it is
        on the screen, so the newest lecture might not appear as the
        newest available podcast.
        """
        start = datetime.strptime(datetime.now().strftime("%Y0101 %H:%M:%S"),
                                  '%Y%m%d %H:%M:%S')
        pubdate = start + timedelta(days=int(self.key().name()))
        return pubdate.strftime(coursera_rss.TIME_FORMAT)

# --------------------------------------------------------------------
# Pages

class HomePage(webapp2.RequestHandler):
    """
    Shows a listing of all courses, including the last time each
    course was updated, and a link to update the course.
    """
    def get(self):
        courses = Course.all()
        template = jinja_environment.get_template('home.html')
        self.response.out.write(template.render({
            'courses': courses
            }))

class CoursePage(webapp2.RequestHandler):
    """
    Takes a course name and shows the RSS feed of that course's
    lectures.
    """
    def get(self):
        course_name = self.request.get('name')
        if course_name is None:
            self.redirect('/home')
        course = db.get(Course.make_key(course_name))
        if course is None:
            template = jinja_environment.get_template('notfound.html')
            self.response.out.write(template.render({
                'name': course_name}))
        else:
            lectures = Lecture.all().ancestor(course)
            lectures = sorted(lectures, key=lambda l: int(l.key().name()))
            template = jinja_environment.get_template('course.xml')
            self.response.out.write(template.render({
                'course': course,
                'lectures': lectures
                }))

class UpdatePage(webapp2.RequestHandler):
    """
    Page for updating data.  If no course is given, we update the
    course listings.  If a course is given, then we update the
    lectures for that course.  If there is no preview for that course,
    then we need to provide a username and password.  The username and
    password will not be saved.
    """
    def get(self):
        name     = self.request.get('name')
        username = self.request.get('username')
        password = self.request.get('password')
        if name is None or name == '':
            all_courses = coursera_rss.all_courses()
            for course in all_courses:
                self.update_course(course)
            self.redirect('/home')
        else:
            matches = coursera_rss.find_course(name)
            if len(matches) == 0:
                template = jinja_environment.get_template('notfound.html')
                self.response.out.write(template.render({
                    'name': name}))
                return
            course = matches[0]
            logging.info("Found course %s" % name)
            course_obj = self.update_course(course)
            lecture_data = coursera_rss.get_preview_lectures(course)
            if lecture_data is None or len(lecture_data) == 0:
                if (username is None or username == "" or
                    password is None or password == ""):
                    template = jinja_environment.get_template('nopreview.html')
                    self.response.out.write(template.render({
                        'name': name}))
                    return
                logging.info("No preview, reading current course info %s" % name)
                lecture_data = coursera_rss.get_current_lectures(course,
                                                                 username,
                                                                 password)
            if lecture_data is None or len(lecture_data) == 0:
                template = jinja_environment.get_template('notfound.html')
                self.response.out.write(template.render({
                    'name': name}))
                return
            logging.info("Got lectures")
            for ii in range(len(lecture_data)):
                (lecture_name, duration, size, mp4url, description) = lecture_data[ii]
                lecture_obj = db.get(Lecture.make_key(name, str(ii)))
                if lecture_obj is None:
                    logging.info("Making lecture %d" % ii)
                    lecture_obj = Lecture(
                        key_name = str(ii),
                        name = lecture_name,
                        duration = duration,
                        size = size,
                        url = mp4url,
                        description = description,
                        parent = course_obj)
                else:
                    logging.info("Updating lecture %d" % ii)
                    lecture_obj.name = lecture_name
                    lecture_obj.duration = duration
                    lecture_obj.size = size
                    lecture_obj.url = mp4url
                    lecture_obj.description = description
                lecture_obj.put()
            # Should remove lectures which are no longer valid?
            course_obj.last_updated = datetime.now()
            course_obj.put()
            self.response.out.write("Updated %d lectures" % len(lecture_data))

    def update_course(self, course):
        instance = coursera_rss.get_current_instance(course)
        course_obj = db.get(Course.make_key(course['short_name']))
        if course_obj is None:
            course_obj = Course(
                key_name      = course['short_name']
                ,full_name    = course['name']
                ,instructor   = course['instructor']
                ,start_date   = '%s/%s/%s' % (instance['start_month'],
                                              instance['start_day'],
                                              instance['start_year'])
                ,url          = instance['home_link']
                ,icon_url     = course['large_icon']
                ,preview_url  = course['preview_link']
                ,description  = course['short_description']
                )
        else:
            course_obj.instructor  = course['instructor']
            course_obj.full_name   = course['name']
            course_obj.icon_url    = course['large_icon']
            course_obj.url         = instance['home_link']
            course_obj.preview_url = course['preview_link']
            course_obj.description = course['short_description']
            course_obj.start_date  = '%s/%s/%s' % (instance['start_month'],
                                                   instance['start_day'],
                                                   instance['start_year'])
        course_obj.put()
        return course_obj

# -------------------------------------------------------------------
# webapp

app = webapp2.WSGIApplication(
    [('/home',        HomePage)
     ,('/course',     CoursePage)
     ,('/update',     UpdatePage)
     ],
    debug=True)
