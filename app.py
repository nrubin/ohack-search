import imaplib
import os , time, logging, re, sys
import email
from flask import Flask, session, request, redirect, url_for, render_template, jsonify, Response
import socket
import chardet
from flask.ext.cache import Cache
import time

class Mailboxes:
    HELPME="Helpme"
    CARPEDIEM="Carpediem"



app = Flask(__name__, static_url_path='')
cache = Cache(config={'CACHE_TYPE': 'simple'})
cache.init_app(app, config={'CACHE_TYPE': 'simple'})
Flask.secret_key = os.environ.get('FLASK_SESSION_KEY', os.environ.get('SECRET_KEY', 'test-key-please-ignore'))

logging.basicConfig( 
    stream=sys.stdout, 
    level=logging.DEBUG, 
    format='"%(asctime)s %(levelname)8s %(name)s - %(message)s"', 
    datefmt='%H:%M:%S' 
) 

PORT = int(os.environ.get('PORT', 5000))
if 'PORT' in os.environ:
    HOSTNAME = 'fwol.in'
    HOST = 'fwol.in'
else:
    HOSTNAME = 'localhost'
    HOST = 'localhost:5000'


app.mail = imaplib.IMAP4_SSL('imap.gmail.com')
app.mail.login(os.environ.get('ARCHIVEEMAIL') or "empty", os.environ.get('ARCHIVEPASSWORD') or "secret")
app.mail.select(Mailboxes.HELPME) # connect to inbox.



def print_timing(func):
    def wrapper(*arg):
        t1 = time.time()
        res = func(*arg)
        t2 = time.time()
        logging.info('%s took %0.3f ms' % (func.func_name, (t2-t1)*1000.0))
        return res
    return wrapper



## Views
@app.route('/')
def search():
    query = request.args.get('query', False,type=str)
    if request.remote_addr:
        onCampus = isAtOlin(request.remote_addr)
    else:
        onCampus = True
    logging.debug("Is At Olin: %s" % onCampus)
    logging.debug("Query:%s" % query)
    logging.debug("app.debug=%s"%app.debug)
    emailIds = []
    if query and (onCampus or app.debug): #if debugging, let the query through!
        t1 = time.time()
        emailIds = searchMail(query)
        logging.debug('Querying %s email-ids took %0.3f ms' % (len(emailIds), (time.time()-t1)*1000.0))

    return render_template('search.html',emails=getEmailBatch(emailIds[:50]), shouldServe=onCampus)


@app.route('/search')
def apiQuery():
    query = request.args.get('query', False,type=str)
    if query:
        emailIds = searchMail(query)
        return jsonify(emails=getEmailBatch(emailIds[:50]))
    else:
        return jsonify({"error":"No query parameter set"}),400 #bad request


@app.route('/mu-c9d98459-e81972b2-54d297df-5e25108c')
def blitzAuthorize():
    return '42'


@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('error.html'), 500


@print_timing
@cache.memoize()
def isAtOlin(remoteAddress):
    try: #occasionally hostname lookup fails
        host,_,_ = socket.gethostbyaddr(remoteAddress)
    except Exception as e:
        return False
    return  'olin' in host

def getEmail(uid):
    typ, data = app.mail.fetch(uid, '(RFC822)')
    return messageToDict(email.message_from_string(data[0][1]) ) 


def messageToDict(msg):
    '''
    Simple dict with body,subject,date to make passing to frontend easy and independent 
    of parsing and sanitization.
    BODY: a list of paragraphs, trimmed, stripped and filtered
    SUBJECT: raw subject
    DATE: raw date
    '''
    if not msg:
        return {"body":"ERROR","subject":"ERROR","date":"ERROR"}
    return {"body" : re.sub("^(\s*\r\n){2,}",'\r\n',getBody(msg)).split('\r\n'),
            "subject" : msg["subject"],
            "date" : msg.get('date')
            }


def getSlices(data):
    '''
    Simple helper to iterate through a list in pairs of two. Expects an even length data
    '''
    for i in range(len(data)/2):
        yield data[i*2:(i*2) + 2]

def getEmailBatch(emailIds):
    '''
    Reduces number of requests via IMAP by constructing one large one, containing
    comma seperated id args, parses and returns all messages specified by emailIds. 
    '''
    if not emailIds or len(emailIds) <= 0:
        return []

    try:
        t1 = time.time()
        res = {}
        queryString = ','.join([numail for numail in emailIds])
        typ, data = app.mail.fetch(queryString, '(RFC822 X-GM-THRID)')
        for d in getSlices(data): #data comes as two by two tuples, [0][1] contains raw data
            msg = email.message_from_string(d[0][1]) 
            m = re.match(r'.*X-GM-THRID (\d+) .*',d[0][0])
            threadId = -1
            if m:
                threadId = m.group(1)

            if not threadId in res:
                res[threadId] = []

            res[threadId].append(
                {"body" : getBody(msg).split('\r\n'),
                 "subject" : msg["subject"],
                 "date" : msg.get('date'),
                 "threadId" : threadId
                })
        logging.debug('GetContent on %s emails took %0.3f ms' % (len(emailIds), (time.time()-t1)*1000.0))
        return res #reverse them, so they are date sorted
    except imaplib.IMAP4.abort as e:
        logging.error(e)
        app.mail = imaplib.IMAP4_SSL('imap.gmail.com')
        app.mail.login(os.environ.get('ARCHIVEEMAIL') or 'empty', os.environ.get('ARCHIVEPASSWORD') or 'secret')
        app.mail.select(Mailboxes.HELPME)
        return getEmailBatch(uids)

def getBody(msg, htmlIfEmpty=True, magick=False):
    '''
    Returns the body of an email.message as plain unicode, extracting only the text MIME type.
    If htmlIfEmpty is specified, if there is not plain/text content, multipart content is
    extracted, in cases such as inline html images in weird mail clients.
    magick is a simple param to ensure that we do not infinitely recurse in situations where 
    multipart/mixed and text/plain produce zero length bodies.
    '''
    res = ''
    for part in msg.walk():
        if part.get_content_type() == "text/plain" or (htmlIfEmpty and part.get_content_type() == "multipart/mixed"): #we don't want the HTML, or attachments
            if part.get_content_charset() is None:
                charset = chardet.detect(str(part))['encoding']
            else:
                charset = part.get_content_charset()
            try:
                res += unicode(part.get_payload(decode=True) or '',str(charset),"ignore")
            except Exception as e:
                logging.error("Decoding error: %s original={%s}"%(e, part.get_payload(decode=True)))
                continue
    res = re.sub("^(\s*\r\n){2,}",'\r\n',res) #remove double line breaks
    if htmlIfEmpty and len(res) ==0 and not magick:
        return getBody(msg,True,True)
    return res.replace("-------------- next part --------------\r\nSkipped content of type text/html","") #not sure why mailman inserts this everywhere


@cache.memoize() #memoize this operation to allow pagination later
def searchMail(query):
    try:
        query = query.translate(None,"\"'") #quote characters are the only literal which will break search
        typ, data = app.mail.search('utf8', '(X-GM-RAW "%s")'% query)
        return [r for r in reversed(data[0].split())] #Google gives them in reverse date order...
    except (imaplib.IMAP4.abort, Exception) as e:
        logging.error(e)
        app.mail = imaplib.IMAP4_SSL('imap.gmail.com')
        app.mail.login(os.environ.get('ARCHIVEEMAIL') or 'empty', os.environ.get('ARCHIVEPASSWORD') or 'secret')
        app.mail.select(Mailboxes.HELPME)
        return searchMail(query)

if __name__ == '__main__':
    # Bind to PORT if defined, otherwise default to 5000.
    app.run(host=HOSTNAME, port=PORT, debug=not os.environ.get('PAPERTRAIL_API_TOKEN',False))
