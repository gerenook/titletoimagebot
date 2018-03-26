#!/usr/bin/env python3

"""meh"""

__version__ = '0.4'
__author__ = 'gerenook'

import logging
import textwrap
import threading
import time
from io import BytesIO
from logging.handlers import TimedRotatingFileHandler
from os import remove

import praw
import requests
from imgurpython import ImgurClient
from imgurpython.helpers.error import (ImgurClientError,
                                       ImgurClientRateLimitError)
from PIL import Image, ImageDraw, ImageFont
from prawcore.exceptions import ResponseException

import apidata


class Meme:
    """Meme class

    :param image: the image (meme)
    :type image: PIL.Image.Image
    """
    font_file = 'segoeui.ttf'
    font_scale_factor = 20

    def __init__(self, image):
        self._meme = image
        self._width, self._height = image.size

    def add_title(self, title):
        """Add title to new whitespace on meme

        :param title: the title to add
        :type title: str
        """
        font = ImageFont.truetype(Meme.font_file, self._width // Meme.font_scale_factor)
        line_height = font.getsize(title)[1]
        # Find the delimiter
        delimiter = None
        for character in title:
            if character in [',', ';', '.']:
                delimiter = character
                break
        # Split title at delimiter and add it back
        # Example:
        # title = 'Roses are red, violets are blue,'
        # texts = ['Roses are red,', 'violets are blue,']
        if delimiter:
            texts = [s.strip() + delimiter for s in title.split(delimiter) if s]
        else:
            texts = textwrap.wrap(title, 45)
        whitespace_height = (line_height * len(texts)) + 10
        new = Image.new('RGB', (self._width, self._height + whitespace_height), '#fff')
        new.paste(self._meme, (0, whitespace_height))
        draw = ImageDraw.Draw(new)
        for i, text in enumerate(texts):
            draw.text((10, i * line_height), text, '#000', font)
        self._width, self._height = new.size
        self._meme = new

    def add_author(self, author):
        """Add /u/author to top right of image

        :param author: the author to add (without /u/)
        :type author: str
        """
        font = ImageFont.truetype(Meme.font_file, self._width // (Meme.font_scale_factor*2))
        text = '/u/' + author
        draw = ImageDraw.Draw(self._meme)
        size = font.getsize(text)
        pos = (self._width - (size[0] + 10), 0)
        draw.text(pos, text, '#000', font)

    def upload(self, imgur):
        """Upload self._meme to imgur

        :param imgur: the imgur api client
        :type imgur: imgurpython.client.ImgurClient
        :returns: imgur url if upload successful, else None
        :rtype: str
        """
        path_png = 'temp.png'
        path_jpg = 'temp.jpg'
        self._meme.save(path_png)
        self._meme.save(path_jpg)
        try:
            response = imgur.upload_from_path(path_png)
        except (ImgurClientError, ImgurClientRateLimitError) as error:
            logging.warning('png upload failed, trying jpg | %s', error)
            try:
                response = imgur.upload_from_path(path_jpg)
            except (ImgurClientError, ImgurClientRateLimitError) as error:
                logging.error('jpg upload failed, returning | %s', error)
                return None
        finally:
            remove(path_png)
            remove(path_jpg)
        return response['link']


class SubmissionThread(threading.Thread):
    """ SubmissionThread class

    Waits for new submissions using subreddit stream

    :param sub: the subreddit(s), default is 'boottoobig'
    :type sub: str
    """

    def __init__(self, sub='boottoobig'):
        threading.Thread.__init__(self)
        self._imgur = ImgurClient(**apidata.imgur)
        self._reddit = praw.Reddit(**apidata.reddit)
        self._sub = sub
        self._template = '[Image with title]({0})\n\n' \
                         '---\n\n' \
                         '^^[remove](https://reddit.com/message/compose/' \
                         '?to=TitleToMemeBot&subject=remove&message={1}) ^^\\(for ^^OP\\) ^^| ' \
                         '^^[feedback](https://reddit.com/message/compose/' \
                         '?to=TitleToMemeBot&subject=feedback) ^^| ' \
                         '^^[source](https://github.com/gerenook/titletomemebot)'

    def _process_submission(self, submission):
        """Generate new image with added title and author, upload to imgur, reply to submission

        :param submission: the reddit submission object
        :type submission: praw.models.reddit.submission.Submission
        """
        title = submission.title
        url = submission.url
        subreddit = submission.subreddit.display_name
        logging.info('Found new submission id:%s title:%s subreddit:%s',
                     submission.id, title, subreddit)
        if url.endswith('.gif'):
            logging.info('Image is animated gif, skipping submission')
            return
        logging.debug('Trying to download image from %s', url)
        try:
            response = requests.get(url)
            img = Image.open(BytesIO(response.content))
        except OSError as error:
            logging.warning('Converting to image failed, trying with <url>.jpg | %s', error)
            try:
                response = requests.get(url + '.jpg')
                img = Image.open(BytesIO(response.content))
            except OSError as error:
                logging.error('Converting to image failed, skipping submission | %s', error)
                return
        if subreddit == 'boottoobig':
            triggers = [',', '.', ';', 'roses']
            if not any(t in title.lower() for t in triggers):
                logging.info('Title is probably not part of rhyme, skipping submission')
                return
        meme = Meme(img)
        meme.add_title(title)
        meme.add_author(submission.author.name)
        logging.debug('Trying to upload image')
        for _ in range(3):
            url = meme.upload(self._imgur)
            if url:
                break
            logging.warning('Upload failed, retrying')
        if not url:
            logging.error('Cannot upload image, skipping submission')
            return
        logging.debug('Creating reply')
        reply = self._template.format(url, '{0}')
        try:
            comment = submission.reply(reply)
        except Exception as error:
            logging.error('Cannot reply, skipping submission')
            return
        logging.debug('Editing comment with remove link')
        comment.edit(reply.format(comment.id))
        logging.info('Successfully processed submission')

    def run(self):
        subreddit = self._reddit.subreddit(self._sub)
        logging.debug('Waiting for new submission...')
        while True:
            try:
                for i, submission in enumerate(subreddit.stream.submissions()):
                    # stream includes past 100 submissions, skip those
                    if i < 100:
                        continue
                    self._process_submission(submission)
                    logging.debug('Waiting for new submission...')
            except (requests.exceptions.ReadTimeout,
                    requests.exceptions.ConnectionError,
                    ResponseException):
                logging.error('Subreddit stream timed out, restarting')
                continue


class MessageThread(threading.Thread):
    """ MessageThread class

    Waits for new messages using inbox stream
    """

    def __init__(self):
        threading.Thread.__init__(self, name=MessageThread.__name__)
        self._reddit = praw.Reddit(**apidata.reddit)

    def _process_remove_message(self, message):
        """Remove comment referenced in message body

        :param message: the remove message
        :type message: praw.models.reddit.message.Message
        """
        comment_id = message.body
        logging.info('Found new remove message id:%s', comment_id)
        logging.debug('Trying to remove comment')
        try:
            comment = self._reddit.comment(comment_id)
            submission_author = comment.submission.author.name
            message_author = message.author.name
            if (message_author == submission_author or
                    message_author == __author__):
                comment.delete()
                logging.info('Successfully deleted comment')
            else:
                logging.info('Authors don\'t match, comment not removed')
        except Exception as error:
            logging.error('Cannot remove comment | %s', error)
        finally:
            message.mark_read()

    def _process_feedback_message(self, message):
        """Forward message to creator

        :param message: the feedback message
        :type message: praw.models.reddit.message.Message
        """
        message_author = message.author.name
        logging.info('Found feedback message from %s', message_author)
        subject = 'TitleToMemeBot feedback from ' + message_author
        body = message.body
        self._reddit.redditor(__author__).message(subject, body)
        message.mark_read()
        logging.info('Forwarded message to author')

    def run(self):
        logging.debug('Waiting for new message...')
        while True:
            try:
                for message in self._reddit.inbox.stream():
                    subject = message.subject.lower()
                    body = message.body.lower()
                    if subject == 'remove':
                        self._process_remove_message(message)
                    elif subject == 'feedback':
                        self._process_feedback_message(message)
                    elif 'good bot' in body and len(body) < 12:
                        logging.debug('Good bot message or comment reply found, marking as read')
                        message.mark_read()
                    elif 'bad bot' in body and len(body) < 12:
                        logging.debug('Bad bot message or comment reply found, marking as read')
                        message.mark_read()
                    logging.debug('Waiting for new message...')
            except (requests.exceptions.ReadTimeout,
                    requests.exceptions.ConnectionError,
                    ResponseException):
                logging.error('Inbox stream timed out, restarting')
                continue


def _setup_logging(level):
    """Setup the root logger

    logs to stdout and to daily log files in ./log/

    :param level: the logging level (e.g. logging.WARNING)
    :type level: int
    """
    console_handler = logging.StreamHandler()
    file_handler = TimedRotatingFileHandler('./log/titletomemebot.log', when='midnight', interval=1)
    file_handler.suffix = '%Y-%m-%d'
    module_loggers = ['requests', 'urllib3', 'prawcore', 'PIL.Image', 'PIL.PngImagePlugin']
    for logger in module_loggers:
        logging.getLogger(logger).setLevel(logging.ERROR)
    logging.basicConfig(format='%(asctime)s %(levelname)s %(threadName)s L%(lineno)d: %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S',
                        level=level,
                        handlers=[console_handler, file_handler])

def main():
    """Main function"""
    _setup_logging(logging.INFO)
    threads = [SubmissionThread(), MessageThread()]
    for thread in threads:
        thread.daemon = True
        thread.start()
    while True:
        time.sleep(1)

if __name__ == '__main__':
    main()
