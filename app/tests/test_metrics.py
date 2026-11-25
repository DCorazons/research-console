from console import metrics


def test_sampler_starts_and_stops(tmp_path):
    stat = tmp_path / 'stat'
    stat.write_text('cpu  10 0 10 80 0 0 0 0 0 0\ncpu1 5 0 5 40 0 0 0 0 0 0\n')
    sampler = metrics.ProcStatSampler(interval=0.05, path=str(stat))
    sampler.start()
    sampler.stop()
    assert not sampler.is_alive()
    assert len(sampler.samples) >= 2
