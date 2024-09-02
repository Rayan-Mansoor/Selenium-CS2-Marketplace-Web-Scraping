FROM python:3.12

WORKDIR /app

COPY . /app

RUN pip install --trusted-host pypi.python.org -r requirements.txt

RUN apt update && \
    apt install -y wget unzip && \
    wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
    apt install -y ./google-chrome-stable_current_amd64.deb && \
    rm google-chrome-stable_current_amd64.deb && \ 
    apt clean

# Expose the port
EXPOSE 10000

# Run Gunicorn instead of Flask development server
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:10000"]