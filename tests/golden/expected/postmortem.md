## Incident: Use nginx

**Status:** resolved · **Duration:** 9s

`sudo nginx -t` failed while working on: Use nginx. Resolved after 2 more step(s), 9s later. [imp-61f28cd04a26#c2] [imp-61f28cd04a26#c4]

### Timeline

- +0s `sudo nginx -t`: failed. `sudo nginx -t` failed: nginx: [emerg] invalid parameter "root" in /etc/nginx/sites-enabled/default:3 [imp-61f28cd04a26#c2]
- +8s `sudo sed -i 's/listen 8080 default_server$/listen 8080 default_server;/' default`: ok. Ran `sudo sed -i 's/listen 8080 default_server$/listen 8080 default_server;/' default`. [imp-61f28cd04a26#c3]
- +9s `sudo nginx -t`: ok. Ran `sudo nginx -t`. [imp-61f28cd04a26#c4]

### Root cause

`sudo nginx -t`: `sudo nginx -t` failed: nginx: [emerg] invalid parameter "root" in /etc/nginx/sites-enabled/default:3 [imp-61f28cd04a26#c2]

```
nginx: [emerg] invalid parameter "root" in /etc/nginx/sites-enabled/default:3
nginx: configuration file /etc/nginx/nginx.conf test failed
```

### Fix

- `sudo sed -i 's/listen 8080 default_server$/listen 8080 default_server;/' default`: Ran `sudo sed -i 's/listen 8080 default_server$/listen 8080 default_server;/' default`. [imp-61f28cd04a26#c3]
- `sudo nginx -t`: Ran `sudo nginx -t`. [imp-61f28cd04a26#c4]

### Lessons

- Watch for: `sudo nginx -t` failed: nginx: [emerg] invalid parameter "root" in /etc/nginx/sites-enabled/default:3 [imp-61f28cd04a26#c2]
