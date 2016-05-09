dpkg-deb --build deb-package
mv deb-package.deb TIKCA_v2.deb
cp TIKCA_v2.deb ~/debs/amd64
cd ~/debs
dpkg-scanpackages amd64 | gzip -9c > amd64/Packages.gz
scp amd64/* nflmhcontrol:


