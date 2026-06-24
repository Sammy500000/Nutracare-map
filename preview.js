// Local preview launcher: starts the server with auth disabled (dev only).
process.env.MAP_NO_AUTH = '1';
require('./server.js');
