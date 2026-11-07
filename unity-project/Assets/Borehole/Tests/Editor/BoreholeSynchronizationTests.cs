using System;
using System.IO;
using NUnit.Framework;
using UnityEngine;

namespace Subsurface.Borehole.Tests
{
    public class BoreholeSynchronizationTests
    {
        [Test]public void TablePreservesOriginalColumnValuesAndOrderWhileGeometrySortsByDepth()
        {
            var data=BoreholeCsv.Parse("top_depth,bottom_depth,lithology,cluster_id\n20,30,Sandstone,alpha\n0,10,Shale,beta");
            Assert.That(data.Headers[3],Is.EqualTo("cluster_id"));Assert.That(data.SourceRows[0].TopFeet,Is.EqualTo(20));Assert.That(data.SourceRows[0].SourceValues[3],Is.EqualTo("alpha"));Assert.That(data.Intervals[0].TopFeet,Is.EqualTo(0));
        }
        const string Header="top_depth,bottom_depth,lithology,top_y,bottom_y\n";
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
