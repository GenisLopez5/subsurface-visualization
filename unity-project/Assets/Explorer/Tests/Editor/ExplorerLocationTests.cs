using NUnit.Framework;
using UnityEngine;

namespace Subsurface.Explorer.Tests
{
    public class ExplorerLocationTests
    {
        [Test]public void ReadsCanonicalRootLocation()
        {
            var entry=new DocumentExplorer.Entry{name="Baltic log",location=new DocumentExplorer.GeoLocation{latitude=58.5,longitude=19.2,label="Baltic Sea"}};
            Assert.That(DocumentExplorer.TryGetLocation(entry,out double latitude,out double longitude,out string label),Is.True);
            Assert.That(latitude,Is.EqualTo(58.5));Assert.That(longitude,Is.EqualTo(19.2));Assert.That(label,Is.EqualTo("Baltic Sea"));
        }

        [Test]public void ReadsStatsAliasesAndGeoJsonCoordinateOrder()
        {
            var aliases=new DocumentExplorer.Entry{stats=new DocumentExplorer.EntryStats{lat=3.139,lon=101.687,location_name="Kuala Lumpur"}};
            Assert.That(DocumentExplorer.TryGetLocation(aliases,out double latitude,out double longitude,out string label),Is.True);
            Assert.That(latitude,Is.EqualTo(3.139));Assert.That(longitude,Is.EqualTo(101.687));Assert.That(label,Is.EqualTo("Kuala Lumpur"));

            var coordinates=new DocumentExplorer.Entry{geo_location=new DocumentExplorer.GeoLocation{coordinates=new[]{-63.5,44.7},name="Offshore Canada"}};
            Assert.That(DocumentExplorer.TryGetLocation(coordinates,out latitude,out longitude,out label),Is.True);
            Assert.That(latitude,Is.EqualTo(44.7));Assert.That(longitude,Is.EqualTo(-63.5));
        }

        [Test]public void MissingOrOutOfRangeCoordinatesAreNotPlotted()
        {
            Assert.That(DocumentExplorer.TryGetLocation(new DocumentExplorer.Entry(),out _,out _,out _),Is.False);
            var invalid=new DocumentExplorer.Entry{location=new DocumentExplorer.GeoLocation{latitude=120,longitude=10,label="Invalid"}};
            Assert.That(DocumentExplorer.TryGetLocation(invalid,out _,out _,out _),Is.False);
        }

        [Test]public void ProjectionUsesEquirectangularWorldCoordinates()
        {
            var bounds=new Rect(10,20,360,180);
            Assert.That(WorldLocationMap.Project(0,0,bounds),Is.EqualTo(new Vector2(190,110)));
            Assert.That(WorldLocationMap.Project(90,-180,bounds),Is.EqualTo(new Vector2(10,20)));
            Assert.That(WorldLocationMap.Project(-90,180,bounds),Is.EqualTo(new Vector2(370,200)));
        }
    }
}
