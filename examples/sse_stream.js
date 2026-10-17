// Live updates in the browser or Node 18+: one event every 5 s with every aircraft in the area.
const es = new EventSource("http://localhost:8000/v1/stream/nearby?lat=40.64&lon=-73.78&radius_nm=40&interval=5");
es.onmessage = (e) => {
  const d = JSON.parse(e.data);
  console.log(`${d.count} aircraft`, d.aircraft.slice(0, 3).map((a) => a.callsign));
};
