const https = require('https');
https.get('https://polymarketrodeo.com/events?limit=200&status=active', res => {
  let data='';
  res.on('data', chunk => data+=chunk);
  res.on('end', () => {
    const events = JSON.parse(data);
    events.filter(e => /elon/i.test(e.title) && /tweet/i.test(e.title)).forEach(e => {
      console.log(e.id, e.title);
    });
  });
}).on('error', (err) => {
  console.error('proxy error', err);
});
