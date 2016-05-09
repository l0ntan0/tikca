locale-gen en_US en_US.UTF-8 de_DE de_DE.UTF-8 
dpkg-reconfigure locales

adduser --system --no-create-home --disabled-login matterhorn
cp tikca.conf /etc/init/tikca.conf
ln -s /etc/init/tikca.conf /etc/init.d/tikca
update-rc.d tikca defaults

mkdir /srv/recordings
chown -R matterhorn /srv/recordings/
touch /var/log/tikca.log
chown matterhorn /var/log/tikca.log

wget "http://ftp.uni-stuttgart.de/ubuntu/pool/main/c/configobj/python3-configobj_5.0.6-1_all.deb"
dpkg -i python3-configobj_5.0.6-1_all.deb

apt-get install python3-six -y
apt-get install gstreamer1.0-plugins-good gir1.2-gstreamer-1.0 python3-dateutil python3-icalendar python3-pycurl curl screen python3-gi -y
apt-get install gstreamer1.0-plugins-bad -y
cp tikca_logrotate.conf /etc/logrotate.d/tikca

mkdir /usr/local/bin/tikca
cp -r ../* /usr/local/bin/tikca

service tikca start
