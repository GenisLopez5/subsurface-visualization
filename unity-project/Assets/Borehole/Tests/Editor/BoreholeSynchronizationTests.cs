using System;
using System.IO;
using NUnit.Framework;
using UnityEngine;

namespace Subsurface.Borehole.Tests
{
    public class BoreholeSynchronizationTests
    {
        [TestCase(800,100)] [TestCase(100,800)] [TestCase(320,240)]
        public void LegendsPreserveTheirIntrinsicAspectRatio(int width,int height)
        {
            var root=new UnityEngine.UIElements.VisualElement();var viewport=new UnityEngine.UIElements.VisualElement{name="documentViewport"};root.Add(viewport);viewport.Add(new UnityEngine.UIElements.VisualElement{name="csvOverlay"});
            var texture=new Texture2D(width,height);var png=texture.EncodeToPNG();UnityEngine.Object.DestroyImmediate(texture);
            using(var legends=new BoreholeLegends(root))
            {
                legends.Bind(png,png);
                var images=UnityEngine.UIElements.UQueryExtensions.Query<UnityEngine.UIElements.Image>(root).ToList();
                Assert.That(images.Count,Is.EqualTo(2));
                foreach(var image in images){Assert.That(image.scaleMode,Is.EqualTo(ScaleMode.ScaleToFit));Assert.That(image.image.width/(double)image.image.height,Is.EqualTo(width/(double)height));}
            }
        }
        [Test]public void TablePreservesOriginalColumnValuesAndOrderWhileGeometrySortsByDepth()
        {
            var data=BoreholeCsv.Parse("top_depth,bottom_depth,lithology,cluster_id\n20,30,Sandstone,alpha\n0,10,Shale,beta");
            Assert.That(data.Headers[3],Is.EqualTo("cluster_id"));Assert.That(data.SourceRows[0].TopFeet,Is.EqualTo(20));Assert.That(data.SourceRows[0].SourceValues[3],Is.EqualTo("alpha"));Assert.That(data.Intervals[0].TopFeet,Is.EqualTo(0));
        }
        [TestCase("aa715df0-ee67-47ee-b700-184bac706b7c")][TestCase("e0971608-6986-4e46-85da-8b2320516f2e")]
        public void RepresentativePreviewKeepsNativeCoordinatesAndReportsDecodeCost(string id)
        {
            string fixtures=Environment.GetEnvironmentVariable("SUBSURFACE_PERF_FIXTURES");if(string.IsNullOrEmpty(fixtures))Assert.Ignore("Set SUBSURFACE_PERF_FIXTURES to the directory containing the documented TIFF fixtures.");
            string path=Path.Combine(fixtures,id+".tif");if(!File.Exists(path))Assert.Ignore("Representative fixture not available.");
            for(int run=0;run<3;run++)
            {
                var timer=System.Diagnostics.Stopwatch.StartNew();var baseline=BoreholeDocumentImage.Load(path);double before=timer.Elapsed.TotalMilliseconds;
                timer.Restart();var pixels=System.Threading.Tasks.Task.Run(()=>BoreholeDocumentImage.ReadPreview(path)).GetAwaiter().GetResult();var preview=pixels.CreateTexture();double after=timer.Elapsed.TotalMilliseconds;
                try{Assert.That(pixels.OriginalWidth,Is.EqualTo(baseline.OriginalWidth));Assert.That(pixels.OriginalHeight,Is.EqualTo(baseline.OriginalHeight));Assert.That(Math.Max(preview.width,preview.height),Is.LessThanOrEqualTo(2048));TestContext.Progress.WriteLine("DOCUMENT_PREVIEW "+id+" run="+run+" baseline_ms="+before.ToString("0.0",System.Globalization.CultureInfo.InvariantCulture)+" preview_ms="+after.ToString("0.0",System.Globalization.CultureInfo.InvariantCulture)+" baseline_pixels="+(baseline.Texture.width*baseline.Texture.height)+" preview_pixels="+(preview.width*preview.height));}
                finally{UnityEngine.Object.DestroyImmediate(baseline.Texture);UnityEngine.Object.DestroyImmediate(preview);}
            }
        }
        [Test]public void DocumentHitTestingRespectsGapsEdgesAndLegendOffset()
        {
            var data=BoreholeCsv.Parse("top_depth,bottom_depth,lithology,top_y,bottom_y\n20,30,Sandstone,400,600\n0,10,Shale,100,200");
            Assert.That(BoreholeImageMap.IntervalAtPixel(data,172,72),Is.EqualTo(0));
            Assert.That(BoreholeImageMap.IntervalAtPixel(data,271.9,72),Is.EqualTo(0));
            Assert.That(BoreholeImageMap.IntervalAtPixel(data,272,72),Is.EqualTo(-1));
            Assert.That(BoreholeImageMap.IntervalAtPixel(data,522,72),Is.EqualTo(2));
            Assert.That(BoreholeImageMap.IntervalAtPixel(data,10,72),Is.EqualTo(-1));
            Assert.That(BoreholeImageMap.IntervalAtPixel(data,double.NaN,72),Is.EqualTo(-1));
        }
        const string Header="top_depth,bottom_depth,lithology,top_y,bottom_y\n";
        [Test]public void LegendDisplacementShiftsBothDirectionsWithoutChangingCsvOrScale()
        {
            var data=BoreholeCsv.Parse(Header+"100,110,Shale,200,300");
            Assert.That(BoreholeImageMap.TryCreate(data,out var map,out var reason,72),Is.True,reason);
            Assert.That(map.ToPixel(100),Is.EqualTo(272));
            Assert.That(map.ToPixel(105),Is.EqualTo(322));
            Assert.That(map.ToDepth(322),Is.EqualTo(105));
            Assert.That(data.Intervals[0].TopPixel,Is.EqualTo(200));
            Assert.That(map.LastPixel-map.FirstPixel,Is.EqualTo(100));
        }
        [Test]public void MappingUsesEachIntervalAndInterpolatesAcrossNoDataGaps()
        {
            var data=BoreholeCsv.Parse(Header+"100,110,Shale,200,300\n120,140,Sandstone,350,750");
            Assert.That(BoreholeImageMap.TryCreate(data,out var map,out var reason),Is.True,reason);
            Assert.That(map.ToPixel(105),Is.EqualTo(250));Assert.That(map.ToPixel(115),Is.EqualTo(325));Assert.That(map.ToPixel(130),Is.EqualTo(550));
            for(double depth=100;depth<=140;depth+=.125)Assert.That(map.ToDepth(map.ToPixel(depth)),Is.EqualTo(depth).Within(.000001));
            Assert.That(map.ToPixel(0),Is.EqualTo(200));Assert.That(map.ToDepth(900),Is.EqualTo(140));
        }
        [TestCase("100,110,Shale,200,300\n110,120,Sandstone,301,400")]
        [TestCase("100,110,Shale,200,300\n120,130,Sandstone,290,400")]
        [TestCase("100,110,Shale,200,200")]
        [TestCase("100,110,Shale,NaN,300")]
        [TestCase("100,110,Shale,,300")]
        public void InvalidPixelMappingKeepsTheColumnButDisablesSynchronization(string rows)
        {var data=BoreholeCsv.Parse(Header+rows);Assert.That(data.SourceIntervalCount,Is.GreaterThan(0));Assert.That(BoreholeImageMap.TryCreate(data,out _,out string reason),Is.False);Assert.That(reason,Is.Not.Empty);}
        [Test]public void DepthOnlyCsvRemainsUsableWithoutAnImageMap()
        {var data=BoreholeCsv.Parse("top_depth,bottom_depth,lithology\n0,10,Shale");Assert.That(BoreholeImageMap.TryCreate(data,out _,out _),Is.False);}
        [Test]public void ProvidedAlleghenyCsvHasAnInvertiblePixelMap()
        {
            var data=BoreholeCsv.Parse(File.ReadAllText("Assets/Borehole/Samples/Allegheny.csv"));Assert.That(data.SourceIntervalCount,Is.EqualTo(131));
            Assert.That(BoreholeImageMap.TryCreate(data,out var map,out var reason),Is.True,reason);Assert.That(map.FirstPixel,Is.EqualTo(11439));Assert.That(map.LastPixel,Is.EqualTo(24270));
            foreach(var row in data.Intervals)if(!row.IsGap){Assert.That(map.ToPixel(row.TopFeet),Is.EqualTo(row.TopPixel).Within(.001));Assert.That(map.ToPixel(row.BottomFeet),Is.EqualTo(row.BottomPixel).Within(.001));}
        }
#if UNITY_EDITOR_WIN
        [Test]public void WindowsTiffDecoderPreservesOriginalDimensionsAndTopOrigin()
        {
            string path=Path.Combine(Application.temporaryCachePath,"Borehole-decoder-fixture.tif");Texture2D texture=null;
            try
            {
                WriteTiff(path,2,3);var page=BoreholeDocumentImage.Load(path);texture=page.Texture;
                Assert.That(page.OriginalWidth,Is.EqualTo(2));Assert.That(page.OriginalHeight,Is.EqualTo(3));
                var rt=RenderTexture.GetTemporary(2,3,0,RenderTextureFormat.ARGB32);var previous=RenderTexture.active;Texture2D readback=null;
                try{Graphics.Blit(texture,rt);RenderTexture.active=rt;readback=new Texture2D(2,3);readback.ReadPixels(new Rect(0,0,2,3),0,0);readback.Apply();Assert.That(readback.GetPixel(0,2).r,Is.GreaterThan(.9f));Assert.That(readback.GetPixel(0,0).b,Is.GreaterThan(.9f));}
                finally{RenderTexture.active=previous;RenderTexture.ReleaseTemporary(rt);if(readback!=null)UnityEngine.Object.DestroyImmediate(readback);}
            }
            finally{if(texture!=null)UnityEngine.Object.DestroyImmediate(texture);if(File.Exists(path))File.Delete(path);}
        }
        [Test]public void TallTiffRetainsCoordinateBasisWhenReducedForGpuLimits()
        {
            string path=Path.Combine(Application.temporaryCachePath,"Borehole-tall-fixture.tif");Texture2D texture=null;
            try{WriteTiff(path,2,20000);var page=BoreholeDocumentImage.Load(path);texture=page.Texture;Assert.That(page.OriginalHeight,Is.EqualTo(20000));Assert.That(texture.height,Is.LessThanOrEqualTo(Math.Min(SystemInfo.maxTextureSize,16384)));}
            finally{if(texture!=null)UnityEngine.Object.DestroyImmediate(texture);if(File.Exists(path))File.Delete(path);}
        }
        [Test]public void TallTiffDetailReadsNativePixelsAtOriginalCoordinates()
        {
            string path=Path.Combine(Application.temporaryCachePath,"Borehole-native-region.tif");
            try
            {
                WriteTiff(path,40,20000);
                var pixels=BoreholeDocumentImage.ReadRegion(path,new RectInt(3,13000,8,1024));
                Assert.That(pixels.Width,Is.EqualTo(8));Assert.That(pixels.Height,Is.EqualTo(1024));
                Assert.That(pixels.OriginalWidth,Is.EqualTo(40));Assert.That(pixels.OriginalHeight,Is.EqualTo(20000));
                // Buffer is Unity bottom-up BGRA: bottom is blue, top is green.
                Assert.That(pixels.Bgra[0],Is.EqualTo(255));Assert.That(pixels.Bgra[1],Is.EqualTo(0));
                int top=(pixels.Height-1)*pixels.Width*4;Assert.That(pixels.Bgra[top+1],Is.EqualTo(255));Assert.That(pixels.Bgra[top],Is.EqualTo(0));
                Assert.Throws<ArgumentException>(()=>BoreholeDocumentImage.ReadRegion(path,new RectInt(39,19999,2,2)));
            }
            finally{if(File.Exists(path))File.Delete(path);}
        }
        [Test]public void DetailOverviewSamplingPreservesTheRequestedRegion()
        {
            string path=Path.Combine(Application.temporaryCachePath,"Borehole-region-level.tif");
            try
            {
                WriteTiff(path,40,20000);var pixels=BoreholeDocumentImage.ReadRegion(path,new RectInt(0,14000,40,2048),2);
                Assert.That(pixels.Width,Is.EqualTo(20));Assert.That(pixels.Height,Is.EqualTo(1024));Assert.That(pixels.Bgra[0],Is.EqualTo(255));
            }
            finally{if(File.Exists(path))File.Delete(path);}
        }
        static void WriteTiff(string path,int width,int height)
        {
            using(var writer=new BinaryWriter(File.Create(path)))
            {
                const int tags=10,bitOffset=8+2+12*tags+4,pixelOffset=bitOffset+6;
                writer.Write((byte)'I');writer.Write((byte)'I');writer.Write((ushort)42);writer.Write(8);writer.Write((ushort)tags);
                void Tag(ushort key,ushort type,int count,int value){writer.Write(key);writer.Write(type);writer.Write(count);writer.Write(value);}
                Tag(256,4,1,width);Tag(257,4,1,height);Tag(258,3,3,bitOffset);Tag(259,3,1,1);Tag(262,3,1,2);Tag(273,4,1,pixelOffset);Tag(277,3,1,3);Tag(278,4,1,height);Tag(279,4,1,width*height*3);Tag(284,3,1,1);writer.Write(0);
                writer.Write((ushort)8);writer.Write((ushort)8);writer.Write((ushort)8);
                for(int y=0;y<height;y++)for(int x=0;x<width;x++){writer.Write((byte)(y<height/3.0?255:0));writer.Write((byte)(y>=height/3.0&&y<height*2/3.0?255:0));writer.Write((byte)(y>=height*2/3.0?255:0));}
            }
        }
#endif
    }
}
